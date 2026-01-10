import os
import time
import datetime
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from fabric import Connection, Config
from dotenv import load_dotenv
from google import genai

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
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

TARGET_PARTITIONS = ["alto", "medio", "normal"]

# Node State Colors
STATE_COLORS = {
    "idle": 0x00ff00,    # Green
    "mixed": 0xffa500,   # Orange
    "alloc": 0xff0000,   # Red
    "drain": 0x808080,   # Grey
    "down": 0x000000,    # Black
    "unknown": 0x808080
}

class SlurmClient:
    """Handles SSH connections and Slurm commands."""
    
    def get_connection(self):
        """Establishes the SSH connection."""
        bastion_config = Config(overrides={'user': BASTION_USER, 'port': BASTION_PORT})
        bastion = Connection(
            host=BASTION_HOST,
            user=BASTION_USER,
            port=BASTION_PORT,
            connect_kwargs={"password": BASTION_PASSWORD}
        )
        head_node = Connection(
            host=HEAD_NODE_HOST,
            user=HEAD_NODE_USER,
            gateway=bastion,
            connect_kwargs={"password": HEAD_NODE_PASSWORD}
        )
        return head_node

    def get_node_states(self):
        """
        Runs sinfo to get high-level state of nodes in target partitions.
        Returns: Dict {node_name: {partition, state, cpus, memory}}
        """
        nodes = {}
        conn = None
        try:
            conn = self.get_connection()
            # %P=Partition, %n=NodeList, %T=State, %c=CPUs, %m=Memory
            result = conn.run('sinfo -o "%P %n %T %c %m"', hide=True)
            if not result.ok:
                return {}

            lines = result.stdout.strip().split('\n')
            if lines and "PARTITION" in lines[0]: lines = lines[1:]

            for line in lines:
                parts = line.split()
                if len(parts) >= 5:
                    part = parts[0].replace("*", "")
                    nodelist = parts[1]
                    state = parts[2]
                    cpus = parts[3]
                    mem = parts[4]

                    if part in TARGET_PARTITIONS:
                        nodes[nodelist] = {
                            "partition": part,
                            "state": state,
                            "cpus": cpus,
                            "memory": mem
                        }
        except Exception as e:
            logger.error(f"Error in sinfo: {e}")
        finally:
            if conn: conn.close()
        return nodes

    def get_node_details(self, node_list):
        """
        Runs scontrol show node <node_list> --future to get precise memory usage.
        Returns: Dict {node_name: {RealMemory, AllocMem, CPUAlloc, CPUTot}}
        """
        details = {}
        conn = None
        try:
            conn = self.get_connection()
            # Use --future to ensure we get full configured specs
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
                        # Extract key-values. Split by space but respecting quotes matches is hard, 
                        # but Slurm output is usually "Key=Value Key=Value"
                        tokens = line.split()
                        for tok in tokens:
                            if "=" in tok:
                                k, v = tok.split("=", 1)
                                if k in ["RealMemory", "AllocMem", "CPUAlloc", "CPUTot", "CPULoad"]:
                                    details[current_node][k] = v
        except Exception as e:
            logger.error(f"Error in scontrol: {e}")
        finally:
            if conn: conn.close()
        return details

    def get_queue_summary(self):
        """Returns active job count and top users."""
        conn = None
        try:
            conn = self.get_connection()
            result = conn.run("squeue -h -o %u", hide=True)
            if not result.ok:
                return 0, {}
            
            users = [u for u in result.stdout.strip().split('\n') if u]
            user_counts = {}
            for u in users:
                user_counts[u] = user_counts.get(u, 0) + 1
            
            return len(users), user_counts
        except Exception as e:
            logger.error(f"Error in squeue: {e}")
            return 0, {}
        finally:
            if conn: conn.close()

# Bot Setup
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix='!', intents=intents)
slurm = SlurmClient()

# Gemini Setup
gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

def summarize_with_gemini(new_nodes_data, queue_data, active_job_count):
    """Uses Gemini 2.5 Flash Lite to generate a fun summary."""
    if not gemini_client:
        return "New resources available! (Enable Gemini for smart summaries)"

    try:
        # Prepare context
        prompt = f"""
        You are an HPC Cluster Assistant.
        
        **New Available Nodes:**
        {new_nodes_data}
        
        **Queue Context:**
        Active Jobs: {active_job_count}
        Top Users: {queue_data}
        
        **Instructions:**
        1. Write a short notification for researchers.
        2. Group by partition.
        3. Highlight capability (high CPU vs high RAM).
        4. Mention queue hogs if relevant.
        5. Be concise, use emojis. No markdown tables.
        """
        
        response = gemini_client.models.generate_content(
            model='gemini-2.0-flash-lite-preview-02-05',
            contents=prompt
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini Error: {e}")
        return "New resources available! (AI Summary Failed)"

# State Tracking for Anti-Spam
previously_free_nodes = set()

@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    monitor_nodes.start()

@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_nodes():
    global previously_free_nodes
    logger.info("Running background monitoring task...")
    
    if not DISCORD_CHANNEL_ID:
        logger.warning("No DISCORD_CHANNEL_ID set. Skipping alerts.")
        return

    channel = bot.get_channel(int(DISCORD_CHANNEL_ID))
    if not channel:
        logger.warning(f"Could not find channel with ID {DISCORD_CHANNEL_ID}")
        return

    nodes = await bot.loop.run_in_executor(None, slurm.get_node_states)
    if not nodes:
        return

    current_free_ids = set()
    for name, data in nodes.items():
        state = data['state'].lower().replace("*", "")
        if "idle" in state or "mixed" in state:
            current_free_ids.add(name)

    # Determine NEW alerts (Diff check)
    newly_free = current_free_ids - previously_free_nodes
    previously_free_nodes = current_free_ids
    
    if not newly_free:
        return

    logger.info(f"New nodes detected: {newly_free}")

    # Fetch details
    details = await bot.loop.run_in_executor(None, slurm.get_node_details, list(newly_free))
    
    # Prepare Data for AI
    ai_node_data = []

    # Generate Embed
    embed = discord.Embed(
        title="🟢 New Resources Available!",
        color=0x00ff00,
        timestamp=datetime.datetime.utcnow()
    )

    for node_name in newly_free:
        node_basic = nodes.get(node_name)
        node_detail = details.get(node_name)
        
        if node_basic and node_detail:
            # Fix 1MB Bug: Convert raw (MB) to GB
            real_mem_mb = int(node_detail.get('RealMemory', 0))
            alloc_mem_mb = int(node_detail.get('AllocMem', 0))
            
            # If scontrol returns 1 or 0 inexplicably, rely on sinfo fallback? 
            # Ideally --future fixed this.
            
            free_mem_gb = (real_mem_mb - alloc_mem_mb) / 1024
            total_mem_gb = real_mem_mb / 1024
            
            cpu_tot = int(node_detail.get('CPUTot', 0))
            cpu_alloc = int(node_detail.get('CPUAlloc', 0))
            free_cpu = cpu_tot - cpu_alloc
            
            ai_node_data.append({
                "name": node_name,
                "partition": node_basic['partition'],
                "free_cpu": free_cpu,
                "free_ram_gb": f"{free_mem_gb:.1f}",
                "total_ram_gb": f"{total_mem_gb:.1f}"
            })

            embed.add_field(
                name=f"🖥️ {node_name} ({node_basic['partition']})",
                value=(
                    f"**Free CPU:** {free_cpu} / {cpu_tot}\n"
                    f"**Free RAM:** {free_mem_gb:.1f}GB / {total_mem_gb:.1f}GB"
                ),
                inline=False
            )
    
    # Get Queue & AI Summary
    total_jobs, users = await bot.loop.run_in_executor(None, slurm.get_queue_summary)
    
    # Generate AI Description
    ai_summary = await bot.loop.run_in_executor(None, summarize_with_gemini, ai_node_data, users, total_jobs)
    
    # Set AI summary as description
    embed.description = ai_summary

    await channel.send(embed=embed)

@bot.command(name='status')
async def status_command(ctx):
    """Shows a visual summary of the cluster."""
    msg = await ctx.send("🔄 Fetching cluster status...")
    nodes = await bot.loop.run_in_executor(None, slurm.get_node_states)
    
    if not nodes:
        await msg.edit(content="❌ Failed to fetch node data.")
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
            state_key = next((k for k in STATE_COLORS if k in state.lower()), "unknown")
            emoji = "🟢" if "idle" in state_key else \
                    "🟠" if "mixed" in state_key else \
                    "🔴" if "alloc" in state_key else \
                    "⚫" if "down" in state_key else "⚪"
            visuals.append(f"{emoji} `{name}`")
        
        embed.add_field(name=f"Partition: {part}", value="\n".join(visuals), inline=False)

    await msg.edit(content=None, embed=embed)

@bot.command(name='inspect')
async def inspect_command(ctx, node_name: str):
    """Detailed stats for a specific node."""
    msg = await ctx.send(f"🔍 Inspecting {node_name}...")
    details = await bot.loop.run_in_executor(None, slurm.get_node_details, [node_name])
    data = details.get(node_name)
    
    if not data:
        await msg.edit(content=f"❌ Could not get details for {node_name}.")
        return

    cpu_alloc = int(data.get('CPUAlloc', 0))
    cpu_tot = int(data.get('CPUTot', 0))
    
    mem_real_mb = int(data.get('RealMemory', 0))
    mem_alloc_mb = int(data.get('AllocMem', 0))
    mem_used_gb = mem_alloc_mb / 1024
    mem_total_gb = mem_real_mb / 1024
    
    load = data.get('CPULoad', 'N/A')

    embed = discord.Embed(title=f"Node: {node_name}", color=0x3498db)
    embed.add_field(name="CPU Usage", value=f"{cpu_alloc}/{cpu_tot} Cores\nLoad: {load}", inline=True)
    embed.add_field(name="Memory Usage", value=f"{mem_used_gb:.1f}GB / {mem_total_gb:.1f}GB Used", inline=True)

    await msg.edit(content=None, embed=embed)

@bot.command(name='queue')
async def queue_command(ctx):
    """Summary of the job queue."""
    total, users = await bot.loop.run_in_executor(None, slurm.get_queue_summary)
    
    embed = discord.Embed(title="Queue Summary", color=0x9b59b6)
    embed.add_field(name="Total Active Jobs", value=str(total), inline=False)
    
    if users:
        sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)
        lines = [f"**{u}**: {c} jobs" for u, c in sorted_users]
        embed.add_field(name="User Activity", value="\n".join(lines[:15]), inline=False)
    else:
        embed.add_field(name="User Activity", value="No active jobs.", inline=False)

    await ctx.send(embed=embed)

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not found in .env")
    else:
        monitor_nodes.change_interval(seconds=CHECK_INTERVAL)
        try:
            bot.run(DISCORD_BOT_TOKEN)
        except discord.errors.PrivilegedIntentsRequired:
            logger.critical("🛑 PRIVILEGED INTENTS MISSING! Enable 'Message Content Intent' in Portal.")
        except discord.errors.LoginFailure:
            logger.critical("🛑 INVALID TOKEN! Check .env")
        except Exception as e:
            logger.critical(f"🛑 CRITICAL ERROR: {e}")
