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
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", -5))

# Job Tracking Config
TARGET_CLUSTER_USER = os.getenv("TARGET_CLUSTER_USER", "carlos")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")

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
        """Establishes the SSH connection with retries."""
        retries = 3
        for attempt in range(retries):
            try:
                bastion_config = Config(overrides={'user': BASTION_USER, 'port': BASTION_PORT})
                bastion = Connection(
                    host=BASTION_HOST,
                    user=BASTION_USER,
                    port=BASTION_PORT,
                    connect_kwargs={
                        "password": BASTION_PASSWORD,
                        "timeout": 15,
                        "banner_timeout": 15,
                        "auth_timeout": 15
                    }
                )
                head_node = Connection(
                    host=HEAD_NODE_HOST,
                    user=HEAD_NODE_USER,
                    gateway=bastion,
                    connect_kwargs={
                        "password": HEAD_NODE_PASSWORD,
                        "timeout": 15,
                        "banner_timeout": 15,
                        "auth_timeout": 15
                    }
                )
                # Test connection lightly
                head_node.open()
                return head_node
            except Exception as e:
                logger.warning(f"Connection attempt {attempt+1}/{retries} failed: {e}")
                time.sleep(5)
        
        raise ConnectionError("Could not establish SSH connection after multiple attempts.")

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

    def get_node_memory_direct(self, node_name):
        """
        SSHs directly into the node to get accurate memory usage (Total, Used, Free).
        Returns: Dict { 'total_gb': float, 'used_gb': float, 'free_gb': float }
        """
        stats = {'total_gb': 0.0, 'used_gb': 0.0, 'free_gb': 0.0}
        conn = None
        try:
            conn = self.get_connection()
            # Command to run on the head node: ssh <node_name> "free -m ..."
            cmd = f"ssh {node_name} \"free -m | grep Mem | awk '{{print \\$2, \\$3, \\$4}}'\""
            
            logger.info(f"Checking memory directly via SSH on {node_name}...")
            result = conn.run(cmd, hide=True, timeout=10)
            
            if result.ok:
                parts = result.stdout.strip().split()
                if len(parts) == 3:
                    total_mb = int(parts[0])
                    used_mb = int(parts[1])
                    free_mb = int(parts[2])
                    
                    stats['total_gb'] = total_mb / 1024.0
                    stats['used_gb'] = used_mb / 1024.0
                    stats['free_gb'] = free_mb / 1024.0
                    return stats
                    
        except Exception as e:
            logger.error(f"Error in direct SSH memory check for {node_name}: {e}")
        finally:
            if conn: conn.close()
        
        return stats

    def get_node_details_fallback(self, node_list):
        """
        Runs scontrol show node <node_list> --future as fallback.
        """
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
                        tokens = line.split()
                        for tok in tokens:
                            if "=" in tok:
                                k, v = tok.split("=", 1)
                                if k in ["RealMemory", "AllocMem", "CPUAlloc", "CPUTot", "CPULoad"]:
                                    details[current_node][k] = v
        except Exception as e:
            logger.error(f"Error in scontrol fallback: {e}")
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

    def get_user_jobs(self, user):
        """
        Gets list of active jobs for a specific user to track completion.
        Returns: Dict {job_id: {name, state, node}}
        """
        jobs = {}
        conn = None
        try:
            conn = self.get_connection()
            # %i=JobID, %j=Name, %T=State, %N=NodeList
            cmd = f"squeue -u {user} -h -o \"%i %j %T %N\""
            result = conn.run(cmd, hide=True)
            
            if result.ok:
                for line in result.stdout.strip().split('\n'):
                    if not line: continue
                    parts = line.split()
                    if len(parts) >= 4:
                        jid = parts[0]
                        name = parts[1]
                        state = parts[2]
                        node = parts[3]
                        
                        jobs[jid] = {
                            "name": name,
                            "state": state,
                            "node": node
                        }
        except Exception as e:
            # logger.error(f"Error fetching user jobs: {e}") 
            # Don't spam error log if just empty
            pass
        finally:
            if conn: conn.close()
        return jobs

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
        # GOAL
        You are an HPC Cluster Analyst. Your task is to transform raw Slurm JSON data into a human-readable Discord summary.

        # CONTEXT & DATA
        - Active Jobs in Queue: {active_job_count}
        - Top Users: {queue_data}
        - Node Data (JSON): {new_nodes_data}

        # RULES (DO NOT VIOLATE)
        1. STATE IDENTIFICATION: Use 🟢 for IDLE nodes and 🟡 for MIXED nodes.
        2. BOTTLENECK LOGIC: If a node has < 4 Free Cores but > 64GB Free RAM, label it as "⚠️ CPU Bottleneck".
        3. NO TABLES: Output only bullet points.
        4. PER-NODE STATS: Explicitly state that RAM stats are "per distinct node".
        5. ERROR HANDLING: If the node data is empty or malformed, respond with: "Resources are shifting; check !status for details."

        # OUTPUT FORMAT
        Format: [Node Name]: [Emoji] [State] | [Free/Total Cores] Cores Free | [Free RAM] GB RAM ([Contextual Note])

        # EXAMPLE
        huk120: 🟡 Mixed | 2/36 Cores Free | 128.5GB RAM (⚠️ CPU Bottleneck) ... others
        """
        
        response = gemini_client.models.generate_content(
            model='gemini-2.0-flash-lite-preview-02-05',
            contents=prompt
        )
        return response.text
    except Exception as e:
        logger.error(f"Gemini Error: {e}")
        return "New resources available! (AI Summary Failed)"

# State Tracking
previously_free_nodes = set()
active_user_jobs = {} # Tracks jobs for TARGET_CLUSTER_USER
is_cluster_online = True # Assumption: Online at start


@bot.event
async def on_ready():
    logger.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
    monitor_nodes.start()

@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_nodes():
    global previously_free_nodes
    global active_user_jobs
    global is_cluster_online
    
    logger.info("Running background monitoring task...")
    
    if not DISCORD_CHANNEL_ID:
        return

    channel = bot.get_channel(int(DISCORD_CHANNEL_ID))
    if not channel:
        return

    try:
        # --- PART 1: NODE MONITORING ---
        nodes = await bot.loop.run_in_executor(None, slurm.get_node_states)
        
        # If we reach here, connection is GOOD
        if not is_cluster_online:
            is_cluster_online = True
            await channel.send("✅ **Cluster Online**: Connection to Head Node restored!")
            logger.info("Cluster back online.")

        if nodes:
            current_free_ids = set()
            for name, data in nodes.items():
                state = data['state'].lower().replace("*", "")
                if "idle" in state or "mixed" in state:
                    current_free_ids.add(name)

            # Determine NEW alerts (Diff check)
            newly_free = current_free_ids - previously_free_nodes
            previously_free_nodes = current_free_ids
            
            if newly_free:
                logger.info(f"New nodes detected: {newly_free}")

                # Prepare Data for AI
                ai_node_data = []
                
                # Generate Embed
                peru_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                embed = discord.Embed(
                    title="🟢 New Resources Available!",
                    color=0x00ff00,
                    timestamp=peru_time
                )

                # Need fallback details for CPU info
                fallback_details = await bot.loop.run_in_executor(None, slurm.get_node_details_fallback, list(newly_free))

                # Sort nodes alphanumerically for cleaner output
                sorted_nodes = sorted(list(newly_free))

                for node_name in sorted_nodes:
                    node_basic = nodes.get(node_name)
                    
                    # 1. Get Memory Direct
                    mem_stats = await bot.loop.run_in_executor(None, slurm.get_node_memory_direct, node_name)
                    
                    # 2. Get CPU from fallback
                    fallback_data = fallback_details.get(node_name, {})
                    cpu_tot = int(fallback_data.get('CPUTot', node_basic.get('cpus', 0)))
                    cpu_alloc = int(fallback_data.get('CPUAlloc', 0))
                    free_cpu = cpu_tot - cpu_alloc

                    # If direct memory failed, try fallback
                    if mem_stats['total_gb'] == 0:
                        real_mem = int(fallback_data.get('RealMemory', 0))
                        alloc_mem = int(fallback_data.get('AllocMem', 0))
                        mem_stats['total_gb'] = real_mem / 1024.0
                        mem_stats['free_gb'] = (real_mem - alloc_mem) / 1024.0

                    ai_node_data.append({
                        "name": node_name,
                        "partition": node_basic['partition'],
                        "state": node_basic['state'],
                        "free_cpu": free_cpu,
                        "free_ram_gb": f"{mem_stats['free_gb']:.1f}",
                        "total_ram_gb": f"{mem_stats['total_gb']:.1f}"
                    })
                
                # Get Queue & AI Summary
                total_jobs, users = await bot.loop.run_in_executor(None, slurm.get_queue_summary)
                ai_summary = await bot.loop.run_in_executor(None, summarize_with_gemini, ai_node_data, users, total_jobs)
                embed.description = ai_summary
                await channel.send(embed=embed)

        # --- PART 2: JOB COMPLETION TRACKING ---
        if TARGET_CLUSTER_USER and DISCORD_USER_ID:
            current_jobs = await bot.loop.run_in_executor(None, slurm.get_user_jobs, TARGET_CLUSTER_USER)
            
            # If we have previous state (active_user_jobs is not empty), check for diff
            # If active_user_jobs is empty, it might be first run. But we should assume
            # if it's empty, user has no jobs. If user HAD jobs and now has NONE, that's a completion.
            
            # Calculate completed jobs: IDs in 'active' but not in 'current'
            completed_ids = set(active_user_jobs.keys()) - set(current_jobs.keys())
            
            for jid in completed_ids:
                job_info = active_user_jobs[jid]
                # Send DM or Channel Ping? User requested Ping.
                msg = (
                    f"🔔 <@{DISCORD_USER_ID}> **Calculation Finished!**\n"
                    f"Your job **'{job_info['name']}'** (ID: {jid}) on **{job_info['node']}** is no longer in the queue.\n"
                    f"*Time: {(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)).strftime('%H:%M:%S')}*"
                )
                await channel.send(msg)
                logger.info(f"Alerted user about job completion: {jid}")

            # Update state
            active_user_jobs = current_jobs

    except Exception as e:
        logger.error(f"Monitoring Loop Error: {e}")
        # If we were online, we are now offline
        if is_cluster_online:
            is_cluster_online = False
            await channel.send(f"⚠️ **Cluster Offline**: Connection lost to {BASTION_HOST}. Retrying...")

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
            state_ = state.lower().replace("*", "")
            emoji = "🟢" if "idle" in state_ else \
                    "🟡" if "mixed" in state_ else \
                    "🔴" if "alloc" in state_ else \
                    "⚫" if "down" in state_ else "⚪"
            
            visuals.append(f"{emoji} `{name}`")
        
        embed.add_field(name=f"Partition: {part}", value="\n".join(visuals), inline=False)

    await msg.edit(content=None, embed=embed)

@bot.command(name='inspect')
async def inspect_command(ctx, *args):
    """Detailed stats for a specific node."""
    # Handle "!inspect huk 120" by joining args
    node_name = "".join(args).strip()
    
    msg = await ctx.send(f"🔍 Inspecting {node_name}...")
    
    # Try Direct SSH first for memory
    mem_stats = await bot.loop.run_in_executor(None, slurm.get_node_memory_direct, node_name)
    
    # Get CPU from scontrol
    fallback_details = await bot.loop.run_in_executor(None, slurm.get_node_details_fallback, [node_name])
    data = fallback_details.get(node_name, {})
    
    if mem_stats['total_gb'] == 0 and not data:
        await msg.edit(content=f"❌ Could not get details for {node_name}.")
        return

    cpu_alloc = int(data.get('CPUAlloc', 0))
    cpu_tot = int(data.get('CPUTot', 0))
    load = data.get('CPULoad', 'N/A')
    
    # Prefer Direct Memory
    if mem_stats['total_gb'] > 0:
        mem_display = f"{mem_stats['used_gb']:.1f}GB / {mem_stats['total_gb']:.1f}GB"
        mem_free = f"{mem_stats['free_gb']:.1f}GB Free"
    else:
        # Fallback
        mem_real_mb = int(data.get('RealMemory', 0))
        mem_alloc_mb = int(data.get('AllocMem', 0))
        mem_display = f"{(mem_alloc_mb/1024):.1f}GB / {(mem_real_mb/1024):.1f}GB"
        mem_free = f"{((mem_real_mb-mem_alloc_mb)/1024):.1f}GB Free"

    embed = discord.Embed(title=f"Node: {node_name}", color=0x3498db)
    embed.add_field(name="CPU Usage", value=f"{cpu_alloc}/{cpu_tot} Cores\nLoad: {load}", inline=True)
    embed.add_field(name="Memory Usage", value=f"{mem_display}\n{mem_free}", inline=True)

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
