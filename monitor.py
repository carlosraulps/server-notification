import os
import time
import datetime
import logging
import asyncio
import json
import io
from logging.handlers import RotatingFileHandler
from typing import Optional, Dict, List, Any

import discord
from discord.ext import commands, tasks
from discord import app_commands
from fabric import Connection, Config
from dotenv import load_dotenv
from google import genai
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --- 1. CONFIGURATION & LOGGING ---

# Load environment variables
load_dotenv()

# Configuration Constants
BASTION_HOST = "gmcan.unmsm.edu.pe"
BASTION_USER = "carlos"
BASTION_PORT = 7722
BASTION_PASSWORD = os.getenv("SSH_PASSWORD_BASTIAO")

HEAD_NODE_HOST = "192.168.16.100"
HEAD_NODE_USER = "carlos"
HEAD_NODE_PASSWORD = os.getenv("SSH_PASSWORD_HUK")

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", -5))

TARGET_CLUSTER_USER = os.getenv("TARGET_CLUSTER_USER", "carlos")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
TARGET_PARTITIONS = ["alto", "medio", "normal"]
STATE_FILE = "state.json"

# Configure Rotating Logging
logger = logging.getLogger("SlurmBot")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)
# Also log to console
console = logging.StreamHandler()
console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(console)

# --- 2. PERSISTENCE LAYER ---

class BotState:
    """Manages persistent state (Jobs, History) using a JSON file."""
    def __init__(self, filename=STATE_FILE):
        self.filename = filename
        self.active_user_jobs: Dict[str, Any] = {}
        self.queue_history: List[Dict[str, Any]] = [] # List of {timestamp, count}
        self.previously_free_nodes = set()
        self.load()

    def load(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, 'r') as f:
                    data = json.load(f)
                    self.active_user_jobs = data.get("active_user_jobs", {})
                    self.queue_history = data.get("queue_history", [])
                    # previously_free_nodes is transient, usually safe to reset on restart
                    logger.info("State loaded from disk.")
            except Exception as e:
                logger.error(f"Failed to load state: {e}")

    def save(self):
        try:
            # Prune history > 7 days (168 hours)
            cutoff = datetime.datetime.now().timestamp() - (7 * 24 * 3600)
            self.queue_history = [
                entry for entry in self.queue_history 
                if entry["timestamp"] > cutoff
            ]
            
            data = {
                "active_user_jobs": self.active_user_jobs,
                "queue_history": self.queue_history
            }
            with open(self.filename, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.error(f"Failed to save state: {e}")

    def add_history_point(self, count: int):
        self.queue_history.append({
            "timestamp": datetime.datetime.now().timestamp(),
            "count": count
        })
        self.save()

# --- 3. SLURM CLIENT (CORE LOGIC) ---

class SlurmClient:
    """Handles SSH connections and Slurm commands."""
    
    def get_connection(self):
        """Establishes the SSH connection with retries."""
        retries = 3
        for attempt in range(retries):
            try:
                bastion = Connection(
                    host=BASTION_HOST, user=BASTION_USER, port=BASTION_PORT,
                    connect_kwargs={
                        "password": BASTION_PASSWORD,
                        "timeout": 15, "banner_timeout": 15, "auth_timeout": 15
                    }
                )
                head_node = Connection(
                    host=HEAD_NODE_HOST, user=HEAD_NODE_USER, gateway=bastion,
                    connect_kwargs={
                        "password": HEAD_NODE_PASSWORD,
                        "timeout": 15, "banner_timeout": 15, "auth_timeout": 15
                    }
                )
                head_node.open() # Test connection
                return head_node
            except Exception as e:
                logger.warning(f"Connection attempt {attempt+1}/{retries} failed: {e}")
                time.sleep(5)
        raise ConnectionError("Could not establish SSH connection.")

    def is_reachable(self) -> bool:
        """Lightweight heartbeat using Bastion TCP check."""
        try:
            conn = Connection(
                host=BASTION_HOST, user=BASTION_USER, port=BASTION_PORT,
                connect_kwargs={
                    "password": BASTION_PASSWORD,
                    "timeout": 5, "banner_timeout": 5, "auth_timeout": 5
                }
            )
            conn.open()
            conn.close()
            return True
        except Exception:
            return False

    def get_node_states(self):
        nodes = {}
        conn = None
        try:
            conn = self.get_connection()
            result = conn.run('sinfo -o "%P %n %T %c %m"', hide=True)
            if not result.ok: return {}

            lines = result.stdout.strip().split('\n')
            if lines and "PARTITION" in lines[0]: lines = lines[1:]

            for line in lines:
                parts = line.split()
                if len(parts) >= 5:
                    part = parts[0].replace("*", "")
                    nodelist, state, cpus, mem = parts[1], parts[2], parts[3], parts[4]
                    if part in TARGET_PARTITIONS:
                        nodes[nodelist] = {
                            "partition": part, "state": state, "cpus": cpus, "memory": mem
                        }
        except Exception as e:
            logger.error(f"Error in sinfo: {e}")
        finally:
            if conn: conn.close()
        return nodes

    def get_node_memory_direct(self, node_name):
        stats = {'total_gb': 0.0, 'used_gb': 0.0, 'free_gb': 0.0}
        conn = None
        try:
            conn = self.get_connection()
            cmd = f"ssh {node_name} \"free -m | grep Mem | awk '{{print \\$2, \\$3, \\$4}}'\""
            result = conn.run(cmd, hide=True, timeout=10)
            if result.ok:
                parts = result.stdout.strip().split()
                if len(parts) == 3:
                    stats['total_gb'] = int(parts[0]) / 1024.0
                    stats['used_gb'] = int(parts[1]) / 1024.0
                    stats['free_gb'] = int(parts[2]) / 1024.0
                    return stats
        except Exception as e:
            logger.error(f"Direct SSH failed for {node_name}: {e}")
        finally:
            if conn: conn.close()
        return stats

    def get_node_details_fallback(self, node_list):
        details = {}
        conn = None
        try:
            conn = self.get_connection()
            cmd = f"scontrol show node {','.join(node_list)} --future"
            result = conn.run(cmd, hide=True)
            if result.ok:
                current_node = None
                for line in result.stdout.split('\n'):
                    line = line.strip()
                    if line.startswith("NodeName="):
                        current_node = line.split()[0].split("=")[1]
                        details[current_node] = {}
                    if current_node:
                        for tok in line.split():
                            if "=" in tok:
                                k, v = tok.split("=", 1)
                                if k in ["RealMemory", "AllocMem", "CPUAlloc", "CPUTot", "CPULoad"]:
                                    details[current_node][k] = v
        except Exception as e:
            logger.error(f"scontrol fallback error: {e}")
        finally:
            if conn: conn.close()
        return details

    def get_queue_summary(self):
        conn = None
        try:
            conn = self.get_connection()
            result = conn.run("squeue -h -o %u", hide=True)
            if not result.ok: return 0, {}
            users = [u for u in result.stdout.strip().split('\n') if u]
            user_counts = {}
            for u in users: user_counts[u] = user_counts.get(u, 0) + 1
            return len(users), user_counts
        except Exception as e:
            logger.error(f"squeue error: {e}")
            return 0, {}
        finally:
            if conn: conn.close()

    def get_user_jobs(self, user):
        jobs = {}
        conn = None
        try:
            conn = self.get_connection()
            cmd = f"squeue -u {user} -h -o \"%i %j %T %N\""
            result = conn.run(cmd, hide=True)
            if result.ok:
                for line in result.stdout.strip().split('\n'):
                    if not line: continue
                    parts = line.split()
                    if len(parts) >= 4:
                        jobs[parts[0]] = {"name": parts[1], "state": parts[2], "node": parts[3]}
        except Exception:
            pass
        finally:
            if conn: conn.close()
        return jobs

# --- 4. GEMINI AI SUMMARIZER ---

gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def summarize_with_gemini(new_nodes_data, queue_data, active_job_count):
    if not gemini_client:
        return "New resources available! (Enable Gemini for smart summaries)"
    
    prompt = f"""
    # GOAL (Deterministic Role)
    You are a strictly deterministic HPC Analyst. Transform Slurm JSON into bullet points.

    # INFORMATION (Input Data) 
    - Nodes Available: {new_nodes_data}
    - Active Queue Jobs: {active_job_count}
    - User Leaderboard: {queue_data}

    # ACTIONS & CONSTRAINTS
    1. Only use 🟢 for IDLE and 🟡 for MIXED.
    2. Define "⚠️ CPU Bottleneck" ONLY if Free Cores < 4 AND Free RAM > 64GB.
    3. Do not invent nodes.
    4. If 'free_ram_gb' is "0.0", report it as "Stale/Check !status".

    # LANGUAGE (Output Format)
    [Node Name]: [Emoji] [State] | [Free CPU]/[Total CPU] Cores | [Free RAM]GB RAM ([Note])

    # TERMINATION CRITERIA
    If data is empty, return only: "No new resources detected."
    """
    try:
        response = gemini_client.models.generate_content(
            model='gemini-2.0-flash-lite-preview-02-05',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"Agent Feedback Error: {str(e)}"

# --- 5. BOT CLASS & SLASH COMMANDS ---

class SlurmMonitorBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=discord.Intents.default())
        self.state = BotState()
        self.slurm = SlurmClient()
        self.is_cluster_online = True

    async def setup_hook(self):
        # Sync slash commands
        await self.tree.sync()
        logger.info("Slash commands synced.")

    async def on_ready(self):
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        if not self.monitor_nodes.is_running():
            self.monitor_nodes.start()

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def monitor_nodes(self):
        logger.info("Running monitoring loop...")
        if not DISCORD_CHANNEL_ID: return
        channel = self.get_channel(int(DISCORD_CHANNEL_ID))
        if not channel: return

        try:
            # 1. Heartbeat
            reachable = await self.loop.run_in_executor(None, self.slurm.is_reachable)
            if not reachable:
                if self.is_cluster_online:
                    self.is_cluster_online = False
                    await channel.send("⚠️ **CRITICAL: Cluster Unreachable**. Monitoring paused.")
                    logger.warning("Cluster went offline.")
                return 

            if not self.is_cluster_online:
                self.is_cluster_online = True
                await channel.send("✅ **Cluster Connection Restored**.")
                logger.info("Cluster back online.")

            # 2. Node Monitoring
            nodes = await self.loop.run_in_executor(None, self.slurm.get_node_states)
            if nodes:
                current_free_ids = set()
                for name, data in nodes.items():
                    if "idle" in data['state'].lower() or "mixed" in data['state'].lower():
                        current_free_ids.add(name)
                
                newly_free = current_free_ids - self.state.previously_free_nodes
                self.state.previously_free_nodes = current_free_ids

                if newly_free:
                    logger.info(f"New nodes: {newly_free}")
                    # Collect detailed data for AI
                    ai_node_data = []
                    fallback_details = await self.loop.run_in_executor(None, self.slurm.get_node_details_fallback, list(newly_free))
                    
                    for node_name in sorted(list(newly_free)):
                        node_basic = nodes.get(node_name)
                        mem_stats = await self.loop.run_in_executor(None, self.slurm.get_node_memory_direct, node_name)
                        
                        fallback_data = fallback_details.get(node_name, {})
                        cpu_tot = int(fallback_data.get('CPUTot', node_basic.get('cpus', 0)))
                        cpu_alloc = int(fallback_data.get('CPUAlloc', 0))
                        
                        # Fallback memory if direct failed
                        if mem_stats['total_gb'] == 0:
                            real = int(fallback_data.get('RealMemory', 0))
                            alloc = int(fallback_data.get('AllocMem', 0))
                            mem_stats['total_gb'] = real / 1024.0
                            mem_stats['free_gb'] = (real - alloc) / 1024.0

                        ai_node_data.append({
                            "name": node_name, "state": node_basic['state'],
                            "free_cpu": cpu_tot - cpu_alloc,
                            "free_ram_gb": f"{mem_stats['free_gb']:.1f}",
                            "total_ram_gb": f"{mem_stats['total_gb']:.1f}"
                        })

                    # Queue Context & AI
                    total_jobs, users = await self.loop.run_in_executor(None, self.slurm.get_queue_summary)
                    
                    # Persistence: Track History
                    self.state.add_history_point(total_jobs)
                    
                    ai_summary = await self.loop.run_in_executor(None, summarize_with_gemini, ai_node_data, users, total_jobs)
                    
                    peru_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                    embed = discord.Embed(
                        title="🟢 New Resources Available!", color=0x00ff00, timestamp=peru_time,
                        description=ai_summary
                    )
                    await channel.send(embed=embed)
            
            # 3. Job Completion Tracking
            if TARGET_CLUSTER_USER and DISCORD_USER_ID:
                current_jobs = await self.loop.run_in_executor(None, self.slurm.get_user_jobs, TARGET_CLUSTER_USER)
                completed = set(self.state.active_user_jobs.keys()) - set(current_jobs.keys())
                
                for jid in completed:
                    job = self.state.active_user_jobs[jid]
                    msg = (f"🔔 <@{DISCORD_USER_ID}> **Job Finished!**\n"
                           f"Job: **'{job['name']}'** (ID: {jid}) on **{job['node']}**\n"
                           f"*Time: {(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)).strftime('%H:%M:%S')}*")
                    await channel.send(msg)
                
                self.state.active_user_jobs = current_jobs
                self.state.save()

        except Exception as e:
            logger.error(f"GENERIC MONITOR ERROR: {e}")

# Instantiate Bot
bot = SlurmMonitorBot()

# --- 6. SLASH COMMANDS ---

@bot.tree.command(name="status", description="Show visual status of the cluster")
async def status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    nodes = await bot.loop.run_in_executor(None, bot.slurm.get_node_states)
    
    if not nodes:
        await interaction.followup.send("❌ Failed to fetch data (Cluster might be offline).")
        return

    partitions = {}
    for name, data in nodes.items():
        p = data['partition']
        if p not in partitions: partitions[p] = []
        partitions[p].append((name, data['state']))

    embed = discord.Embed(title="📊 Cluster Status", color=0x3498db)
    for part, nlist in partitions.items():
        visuals = []
        for name, state in nlist:
            s = state.lower().replace("*", "")
            emoji = "🟢" if "idle" in s else "🟡" if "mixed" in s else "🔴" if "alloc" in s else "⚫"
            visuals.append(f"{emoji} `{name}`")
        embed.add_field(name=f"Partition: {part}", value="\n".join(visuals), inline=False)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="inspect", description="Check exact specs of a node")
@app_commands.describe(node="Name of the node (e.g., huk120)")
async def inspect(interaction: discord.Interaction, node: str):
    await interaction.response.defer(ephemeral=True)
    
    mem_stats = await bot.loop.run_in_executor(None, bot.slurm.get_node_memory_direct, node)
    fallback = await bot.loop.run_in_executor(None, bot.slurm.get_node_details_fallback, [node])
    data = fallback.get(node, {})

    if mem_stats['total_gb'] == 0 and not data:
        await interaction.followup.send(f"❌ Could not find node `{node}`.")
        return

    cpu_alloc = data.get('CPUAlloc', '?')
    cpu_tot = data.get('CPUTot', '?')
    
    if mem_stats['total_gb'] > 0:
        mem_str = f"{mem_stats['used_gb']:.1f}/{mem_stats['total_gb']:.1f} GB"
        mem_free = f"{mem_stats['free_gb']:.1f} GB Free"
    else:
        real = int(data.get('RealMemory', 0))
        mem_str = f"{real/1024:.1f} GB (Reported)"
        mem_free = "Unknown (Direct SSH failed)"

    embed = discord.Embed(title=f"🔍 Node: {node}", color=0x3498db)
    embed.add_field(name="CPU", value=f"{cpu_alloc}/{cpu_tot} Cores", inline=True)
    embed.add_field(name="RAM", value=f"{mem_str}\n({mem_free})", inline=True)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="queue", description="Show active jobs summary")
async def queue(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    total, users = await bot.loop.run_in_executor(None, bot.slurm.get_queue_summary)
    
    embed = discord.Embed(title="Queue Summary", color=0x9b59b6)
    embed.add_field(name="Total Jobs", value=str(total), inline=False)
    
    if users:
        sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)[:15]
        lines = [f"**{u}**: {c}" for u, c in sorted_users]
        embed.add_field(name="Top Users", value="\n".join(lines), inline=False)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="history", description="Graph of job activity (Last 24h)")
async def history(interaction: discord.Interaction):
    await interaction.response.defer()
    
    history_data = bot.state.queue_history
    if not history_data:
        await interaction.followup.send("No history data available yet.")
        return

    # Prepare Data
    times = [datetime.datetime.fromtimestamp(d['timestamp']) for d in history_data]
    counts = [d['count'] for d in history_data]

    # Plot
    # Plot Style: Dark Mode
    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(10, 5))
    
    # Plot Data
    ax.plot(times, counts, marker='o', linestyle='-', color='#00ff9d', linewidth=2, markersize=4)
    
    # Styling
    ax.set_title("Active Job History (Last 7 Days)", fontsize=14, color='white', pad=15)
    ax.set_xlabel("Time", fontsize=10, color='#cccccc')
    ax.set_ylabel("Active Jobs", fontsize=10, color='#cccccc')
    ax.grid(True, linestyle=':', alpha=0.3, color='#555555')
    
    # Date Formatting
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    fig.autofmt_xdate()
    
    # Remove frames
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_color('#555555')
    ax.spines['left'].set_color('#555555')

    # Save to Buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close()

    file = discord.File(buf, filename="history.png")
    await interaction.followup.send(file=file)

# --- 7. MAIN ENTRY ---

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN missing in .env")
    else:
        bot.monitor_nodes.change_interval(seconds=CHECK_INTERVAL)
        bot.run(DISCORD_BOT_TOKEN)
