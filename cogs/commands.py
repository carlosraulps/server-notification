import discord
from discord import app_commands
from discord.ext import commands
import logging

logger = logging.getLogger("SlurmBot")

class Commands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="status", description="Show visual status of the cluster")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        nodes = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_states)
        
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

    @app_commands.command(name="inspect", description="Detective Mode: Check deep node details")
    @app_commands.describe(node="Node Name or Number (e.g. 120)")
    async def inspect(self, interaction: discord.Interaction, node: str):
        await interaction.response.defer(ephemeral=True)
        
        # Smart Name Resolution
        full_node_name = self.bot.slurm.resolve_node_name(node)
        
        # Get Stats
        mem_stats = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_memory_direct, full_node_name)
        fallback = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_node_details_fallback, [full_node_name])
        data = fallback.get(full_node_name, {})
        
        # Get State for Detective Logic
        # We need fresh state, or try to infer from fallback
        state = data.get("State", "UNKNOWN") 
        
        # Detective Context
        context_str = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_detective_info, full_node_name, state)

        # Formatting
        cpu_alloc = data.get('CPUAlloc', '?')
        cpu_tot = data.get('CPUTot', '?')
        
        if mem_stats['total_gb'] > 0:
            mem_str = f"{mem_stats['used_gb']:.1f}/{mem_stats['total_gb']:.1f} GB"
        else:
            real = int(data.get('RealMemory', 0))
            mem_str = f"{real/1024:.1f} GB (Reported)"

        embed = discord.Embed(title=f"🕵️ Detective: {full_node_name}", color=0x9b59b6)
        embed.add_field(name="Activity", value=context_str, inline=False)
        embed.add_field(name="CPU", value=f"{cpu_alloc}/{cpu_tot} Cores", inline=True)
        embed.add_field(name="RAM", value=mem_str, inline=True)
        
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="queue", description="Show active jobs summary")
    async def queue(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        total, users = await self.bot.loop.run_in_executor(None, self.bot.slurm.get_queue_summary)
        
        embed = discord.Embed(title="Queue Summary", color=0x9b59b6)
        embed.add_field(name="Total Jobs", value=str(total), inline=False)
        
        if users:
            sorted_users = sorted(users.items(), key=lambda x: x[1], reverse=True)[:15]
            lines = [f"**{u}**: {c}" for u, c in sorted_users]
            embed.add_field(name="Top Users", value="\n".join(lines), inline=False)
        
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Commands(bot))
    logger.info("Commands Cog Loaded.")
