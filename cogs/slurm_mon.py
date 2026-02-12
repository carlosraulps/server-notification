import discord
from discord.ext import commands, tasks
import logging
import os
import datetime
import json
from google import genai

logger = logging.getLogger("SlurmBot")

# Gemini Setup
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 300))
TIMEZONE_OFFSET = int(os.getenv("TIMEZONE_OFFSET", -5))

TARGET_CLUSTER_USER = os.getenv("TARGET_CLUSTER_USER", "carlos")
DISCORD_USER_ID = os.getenv("DISCORD_USER_ID")
DISCORD_CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")

class SlurmMon(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.previously_free_nodes = set()
        self.active_user_jobs = {} # For "My Job Finished"
        self.is_cluster_online = True
        
        # Simple Persistence for active_user_jobs only (lightweight)
        self.state_file = "data/job_state.json"
        self.load_state()

        if GEMINI_API_KEY:
            self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        else:
            self.gemini_client = None

    def load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    self.active_user_jobs = json.load(f)
            except: pass
    
    def save_state(self):
        try:
            with open(self.state_file, 'w') as f:
                json.dump(self.active_user_jobs, f)
        except: pass

    def cog_unload(self):
        self.monitor_nodes.cancel()

    async def cog_load(self):
        self.monitor_nodes.start()

    def summarize_with_gemini(self, new_nodes_data, queue_data, active_job_count):
        if not self.gemini_client:
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
            response = self.gemini_client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=prompt
            )
            return response.text
        except Exception as e:
            return f"Agent Feedback Error: {str(e)}"

    @tasks.loop(seconds=CHECK_INTERVAL)
    async def monitor_nodes(self):
        logger.info("Running monitoring loop...")
        if not DISCORD_CHANNEL_ID: return
        channel = self.bot.get_channel(int(DISCORD_CHANNEL_ID))
        if not channel: return

        try:
            # 1. Heartbeat
            reachable = await self.bot.loop.run_in_executor(None, self.bot.slurm.is_reachable)
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
            nodes = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_states)
            if nodes:
                # --- ANALYTICS LOGGING ---
                # Count states for CSV
                idle_c = mixed_c = alloc_c = down_c = 0
                for _, data in nodes.items():
                    s = data['state'].lower().replace("*", "")
                    if "idle" in s: idle_c += 1
                    elif "mixed" in s: mixed_c += 1
                    elif "alloc" in s: alloc_c += 1
                    else: down_c += 1
                
                # Call Analytics Cog
                analytics_cog = self.bot.get_cog("Analytics")
                if analytics_cog:
                    analytics_cog.log_status(idle_c, mixed_c, alloc_c, down_c)
                    
                    # Log granular node states for Heatmap
                    # Create map {node: state}
                    node_state_map = {n: d['state'] for n, d in nodes.items()}
                    analytics_cog.log_node_states(node_state_map)
                # -------------------------

                current_free_ids = set()
                for name, data in nodes.items():
                    if "idle" in data['state'].lower() or "mixed" in data['state'].lower():
                        current_free_ids.add(name)
                
                newly_free = current_free_ids - self.previously_free_nodes
                self.previously_free_nodes = current_free_ids

                if newly_free:
                    logger.info(f"New nodes: {newly_free}")
                    ai_node_data = []
                    fallback_details = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_details_fallback, list(newly_free))
                    
                    for node_name in sorted(list(newly_free)):
                        node_basic = nodes.get(node_name)
                        mem_stats = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_memory_direct, node_name)
                        
                        fallback_data = fallback_details.get(node_name, {})
                        cpu_tot = int(fallback_data.get('CPUTot', node_basic.get('cpus', 0)))
                        cpu_alloc = int(fallback_data.get('CPUAlloc', 0))
                        
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

                    total_jobs, users = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_queue_summary)
                    
                    ai_summary = await self.bot.loop.run_in_executor(None, self.summarize_with_gemini, ai_node_data, users, total_jobs)
                    
                    peru_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)
                    embed = discord.Embed(
                        title="🟢 New Resources Available!", color=0x00ff00, timestamp=peru_time,
                        description=ai_summary
                    )
                    await channel.send(embed=embed)
            
            # 3. Job Completion Tracking
            if TARGET_CLUSTER_USER and DISCORD_USER_ID:
                current_jobs = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_user_jobs, TARGET_CLUSTER_USER)
                completed = set(self.active_user_jobs.keys()) - set(current_jobs.keys())
                
                for jid in completed:
                    job = self.active_user_jobs[jid]
                    msg = (f"🔔 <@{DISCORD_USER_ID}> **Job Finished!**\n"
                           f"Job: **'{job['name']}'** (ID: {jid}) on **{job['node']}**\n"
                           f"*Time: {(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TIMEZONE_OFFSET)).strftime('%H:%M:%S')}*")
                    await channel.send(msg)
                
                self.active_user_jobs = current_jobs
                self.save_state()

        except Exception as e:
            logger.error(f"GENERIC MONITOR ERROR: {e}")

    @monitor_nodes.before_loop
    async def before_monitor(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(SlurmMon(bot))
    logger.info("SlurmMon Cog Loaded.")
