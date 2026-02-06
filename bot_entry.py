import discord
from discord.ext import commands
import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Load Environment
load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# Setup Logging
logger = logging.getLogger("SlurmBot")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler("bot.log", maxBytes=5*1024*1024, backupCount=2)
handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(handler)
console = logging.StreamHandler()
console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
logger.addHandler(console)

# Dependency Injection for Cogs
from utils.slurm_client import SlurmClient

class SlurmBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.slurm = SlurmClient()

    async def setup_hook(self):
        # Load Cogs
        await self.load_extension("cogs.analytics")
        await self.load_extension("cogs.commands")
        await self.load_extension("cogs.slurm_mon")
        
        # Sync Slash Commands
        await self.tree.sync()
        logger.info("All Cogs Loaded and Tree Synced.")

    async def on_ready(self):
        logger.info(f"Bot Online as {self.user} (ID: {self.user.id})")

async def main():
    bot = SlurmBot()
    async with bot:
        await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    if not DISCORD_BOT_TOKEN:
        logger.critical("No Token Found!")
        exit(1)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
