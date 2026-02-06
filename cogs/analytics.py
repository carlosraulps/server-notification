import discord
from discord import app_commands
from discord.ext import commands
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import io
import os
import datetime
import logging

logger = logging.getLogger("SlurmBot")
HISTORY_FILE = "data/history.csv"

class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ensure_history_file()

    def ensure_history_file(self):
        """Creates CSV if missing, or rotates if new month."""
        if not os.path.exists(HISTORY_FILE):
             with open(HISTORY_FILE, 'w') as f:
                f.write("Timestamp,Idle,Mixed,Alloc,Down\n")
        else:
             # Check for monthly rotation (Simple check: File mtime month vs current month)
             # Ideally, we read the first line timestamp, but mtime is faster for "stale file" check
             mtime = os.path.getmtime(HISTORY_FILE)
             file_date = datetime.datetime.fromtimestamp(mtime)
             now = datetime.datetime.now()
             
             if file_date.month != now.month:
                 archive_name = f"data/history_{file_date.strftime('%Y_%m')}.csv"
                 os.rename(HISTORY_FILE, archive_name)
                 logger.info(f"Rotated history file to {archive_name}")
                 with open(HISTORY_FILE, 'w') as f:
                    f.write("Timestamp,Idle,Mixed,Alloc,Down\n")

    def log_status(self, idle, mixed, alloc, down):
        """Appends a new row to the CSV."""
        try:
            now_ts = datetime.datetime.now().timestamp()
            with open(HISTORY_FILE, 'a') as f:
                f.write(f"{now_ts},{idle},{mixed},{alloc},{down}\n")
        except Exception as e:
            logger.error(f"Failed to log analytics: {e}")

    @app_commands.command(name="history", description="Graph of Cluster Capacity (Last 7 Days)")
    async def history(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            df = pd.read_csv(HISTORY_FILE)
            if df.empty:
                 await interaction.followup.send("No history data available yet.")
                 return

            # Convert Timestamp to Datetime
            df['Datetime'] = pd.to_datetime(df['Timestamp'], unit='s')
            
            # Filter Last 7 Days
            cutoff = datetime.datetime.now() - datetime.timedelta(days=7)
            df = df[df['Datetime'] > cutoff]
            
            if df.empty:
                await interaction.followup.send("No recent data (Last 7 days empty).")
                return

            # Plotting Stacked Area Chart
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 5))
            
            x = df['Datetime']
            y_idle = df['Idle']
            y_mixed = df['Mixed']
            y_alloc = df['Alloc']
            # y_down = df['Down'] # Optional to exclude or include
            
            # Colors: Green (Idle), Yellow (Mixed), Red (Alloc)
            colors = ['#00ff00', '#ffcc00', '#ff0000']
            labels = ['Idle', 'Mixed', 'Allocated']
            
            ax.stackplot(x, y_idle, y_mixed, y_alloc, labels=labels, colors=colors, alpha=0.8)
            
            # Styling
            ax.set_title("Cluster Capacity Overview (Last 7 Days)", fontsize=14, color='white', pad=15)
            ax.set_xlabel("Time", fontsize=10, color='#cccccc')
            ax.set_ylabel("Node Count", fontsize=10, color='#cccccc')
            ax.legend(loc='upper left', fontsize='small')
            ax.grid(True, linestyle=':', alpha=0.3)
            
            # Date Formatting
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H'))
            fig.autofmt_xdate()
            
            # Remove frames
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)

            # Save
            buf = io.BytesIO()
            plt.savefig(buf, format='png')
            buf.seek(0)
            plt.close()
            
            file = discord.File(buf, filename="history.png")
            await interaction.followup.send(file=file)

        except Exception as e:
            logger.error(f"Error generating graph: {e}")
            await interaction.followup.send(f"❌ Failed to generate graph: {e}")

async def setup(bot):
    await bot.add_cog(Analytics(bot))
    logger.info("Analytics Cog Loaded.")
