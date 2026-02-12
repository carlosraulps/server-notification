import discord
from discord import app_commands
from discord.ext import commands
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.colors as mcolors
import io
import os
import datetime
import logging
import json

logger = logging.getLogger("SlurmBot")
HISTORY_FILE = "data/history.csv"
NODE_HISTORY_FILE = "data/node_history.jsonl"

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

    def log_node_states(self, nodes_data: dict):
        """
        Appends a line to node_history.jsonl.
        nodes_data: { "huk01": "idle", "huk02": "mixed*"}
        Mapped to ints: 0=Idle, 1=Mixed, 2=Alloc, 3=Down
        """
        try:
            state_map = {}
            for node, state in nodes_data.items():
                s = state.lower().replace("*", "")
                if "idle" in s: val = 0
                elif "mixed" in s: val = 1
                elif "alloc" in s: val = 2
                else: val = 3
                state_map[node] = val
            
            entry = {
                "timestamp": datetime.datetime.now().timestamp(),
                "nodes": state_map
            }
            
            with open(NODE_HISTORY_FILE, 'a') as f:
                f.write(json.dumps(entry) + "\n")
                
        except Exception as e:
             logger.error(f"Failed to log node states: {e}")

    @app_commands.command(name="history", description="Graph of Cluster Capacity (Last 24h)")
    async def history(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        try:
            df = pd.read_csv(HISTORY_FILE)
            if df.empty:
                 await interaction.followup.send("No history data available yet.")
                 return

            # Convert Timestamp to Datetime
            df['Datetime'] = pd.to_datetime(df['Timestamp'], unit='s')
            
            # Filter Last 24 Hours
            cutoff = datetime.datetime.now() - datetime.timedelta(hours=24)
            df = df[df['Datetime'] > cutoff]
            
            if df.empty:
                await interaction.followup.send("No recent data (Last 24h empty).")
                return

            # Plotting Stacked Area Chart
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(10, 5))
            
            x = df['Datetime']
            y_idle = df['Idle']
            y_mixed = df['Mixed']
            y_alloc = df['Alloc']
            y_down = df['Down']
            
            # Colors: Green, Yellow, Red, Grey (User Requested)
            colors = ['#4CAF50', '#FFC107', '#F44336', '#9E9E9E']
            labels = ['Idle', 'Mixed', 'Allocated', 'Down']
            
            ax.stackplot(x, y_idle, y_mixed, y_alloc, y_down, labels=labels, colors=colors, alpha=0.9)
            
            # Styling
            ax.set_title("Cluster Capacity & Health (24h)", fontsize=14, color='white', pad=15)
            ax.set_xlabel("Time", fontsize=10, color='#cccccc')
            ax.set_ylabel("Node Count", fontsize=10, color='#cccccc')
            ax.legend(loc='upper left', fontsize='small', framealpha=0.2)
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

    @app_commands.command(name="heatmap", description="Heatmap of Node Utilization (Last 24h)")
    async def heatmap(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        if not os.path.exists(NODE_HISTORY_FILE):
             await interaction.followup.send("No heatmap data available yet.")
             return

        try:
            # Read JSONL
            data = []
            with open(NODE_HISTORY_FILE, 'r') as f:
                for line in f:
                    try: data.append(json.loads(line))
                    except: pass
            
            if not data:
                await interaction.followup.send("No heatmap data available.")
                return

            # Prepare DataFrame
            # Rows: Nodes, Cols: Time
            timestamps = []
            node_states = {} # {node_name: [states...]}
            
            # First pass: collect all unique node names and times
            for entry in data:
                ts = datetime.datetime.fromtimestamp(entry['timestamp'])
                # Filter last 24h
                if ts < datetime.datetime.now() - datetime.timedelta(hours=24):
                    continue
                
                timestamps.append(ts)
                for node, state in entry['nodes'].items():
                    if node not in node_states:
                        node_states[node] = []
            
            if not timestamps:
                await interaction.followup.send("No recent data (Last 24h).")
                return

            # Re-iterate to fill data (handle missing nodes in some timesteps gracefully)
            # Actually clearer to just build a list of dicts and let DataFrame handle it
            rows = []
            for entry in data:
                ts = datetime.datetime.fromtimestamp(entry['timestamp'])
                if ts < datetime.datetime.now() - datetime.timedelta(hours=24):
                    continue
                
                row = entry['nodes'].copy()
                row['timestamp'] = ts
                rows.append(row)
            
            df = pd.DataFrame(rows)
            if df.empty:
                 await interaction.followup.send("No data to plot.")
                 return
                 
            df.set_index('timestamp', inplace=True)
            # df is now: index=Time, cols=Nodes. Values=0,1,2,3 or NaN
            
            # Fill NaN with 3 (Down/Unknown)
            df.fillna(3, inplace=True)
            
            # Transpose for Heatmap (Y=Nodes, X=Time)
            df_t = df.transpose()
            # Sort nodes alphabetically
            df_t.sort_index(inplace=True)

            # Plotting
            plt.style.use('dark_background')
            fig, ax = plt.subplots(figsize=(12, 8))
            
            # Define Colormap: 0=Green, 1=Yellow, 2=Red, 3=Grey
            cmap = mcolors.ListedColormap(['#4CAF50', '#FFC107', '#F44336', '#212121'])
            bounds = [-0.5, 0.5, 1.5, 2.5, 3.5]
            norm = mcolors.BoundaryNorm(bounds, cmap.N)
            
            # Show image
            # aspect='auto' allows non-square pixels to fill space
            im = ax.imshow(df_t, cmap=cmap, norm=norm, aspect='auto', interpolation='nearest', 
                           extent=[mdates.date2num(df.index[0]), mdates.date2num(df.index[-1]), 0, len(df_t)])

            # Formatting Axes
            ax.xaxis_date()
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
            fig.autofmt_xdate()
            
            # Y-Axis Labels
            ax.set_yticks(range(len(df_t)))
            # imshow draws from top (0) to bottom (N) by default? No, origin is usually upper or lower.
            # Let's clean up y-ticks. maptlotlib imshow logic:
            # We used extent, so Y is 0..len.
            # Let's just set yticks to integers 0.5, 1.5... to center?
            # Easiest way with pandas plotting or seaborn is easier, but sticking to pure matplotlib for control
            
            # Correction: imshow without extent plots index 0 at top (usually).
            # Let's rely on standard tick labeling
            ax.set_yticks([i + 0.5 for i in range(len(df_t))])
            # The labels should match the data order. df_t is sorted.
            # If origin is upper (default), index 0 is top.
            # If sorted A-Z, A is top.
            ax.set_yticklabels(df_t.index[::-1]) # Reverse if needed? Let's verify defaults. usually 0 is top.
            
            ax.set_title("Node Utilization Heatmap (Last 24h)", fontsize=16, color='white', pad=20)
            ax.set_xlabel("Time", fontsize=12)
            
            # Custom Legend
            patches = [
                plt.Rectangle((0,0),1,1, color='#4CAF50', label='Idle'),
                plt.Rectangle((0,0),1,1, color='#FFC107', label='Mixed'),
                plt.Rectangle((0,0),1,1, color='#F44336', label='Alloc'),
                plt.Rectangle((0,0),1,1, color='#212121', label='Down/Unk')
            ]
            ax.legend(handles=patches, bbox_to_anchor=(1.05, 1), loc='upper left')
            
            plt.tight_layout()

            # Save
            buf = io.BytesIO()
            plt.savefig(buf, format='png', bbox_inches='tight')
            buf.seek(0)
            plt.close()
            
            file = discord.File(buf, filename="heatmap.png")
            await interaction.followup.send(file=file)


        except Exception as e:
            logger.error(f"Error generating heatmap: {e}")
            await interaction.followup.send(f"❌ Failed to generate heatmap: {e}")
            # print stack trace for debug
            import traceback
            traceback.print_exc()

async def setup(bot):
    await bot.add_cog(Analytics(bot))
    logger.info("Analytics Cog Loaded.")
