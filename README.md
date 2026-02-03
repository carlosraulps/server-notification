# ⚡ AI-Powered Slurm Cluster Monitor

A sophisticated Discord Bot designed to monitor High-Performance Computing (HPC) clusters managed by Slurm. It bridges the gap between complex terminal outputs and user-friendly notifications, utilizing **Google Gemini AI** to provide human-readable summaries of cluster resources.

## 🌟 Key Features

### 🧠 AI-Enhanced Alerts
*   **Gemini 2.5 Flash Lite "Analyst"**: Instead of raw numbers, get intelligent summaries like "huk120 is wide open with 128GB RAM" or warnings like "⚠️ High CPU Load (RAM available)".
*   **Smart Parsing**: Converts raw Slurm data into concise, emoji-coded updates.

### 🔔 Job Completion Notifications
*   **Personal Pings**: Configure the bot to track *your* specific Slurm user (`squeue -u`).
*   **Instant Alert**: Get a Discord ping (`@User`) the moment your specific job finishes or disappears from the queue.
*   **Timezone Aware**: Calculation finish times are adjusted to your local timezone (e.g., Peru/UTC-5).

### 🛠️ Hardware Accuracy (The "Direct Check")
*   **Fixes Slurm Reporting Bugs**: Bypasses often inaccurate `scontrol` memory reports by SSHing directly into compute nodes (`ssh <node> free -m`) to get exact available physical RAM.
*   **Strict State Logic**: 
    *   🟢 **Idle**: 0 Cores used.
    *   🟡 **Mixed**: Partial load.
    *   🔴 **Alloc**: Fully busy.

### 💻 Hybrid Discord Interface
*   **Real-time Background Loop**: Checks the cluster every `N` seconds (default: 300s).
*   **Interactive Commands**: 
    *   `!status`: Visual dashboard of all nodes and partitions.
    *   `!inspect <node>`: Deep dive into a specific node's CPU load and Memory usage.
    *   `!queue`: Summary of active jobs and top users.

### 🚀 One-Click Deployment
*   **Automated Updates**: `deploy.py` handles pulling code, updating dependencies, copying Systemd services, and restarting the bot—all in one command.

---

## 📂 Project Structure

```
├── monitor.py              # 🧠 Main Application Logic (Slurm Client, Discord Bot, AI Integration)
├── deploy.py               # 🚀 Deployment Automation Script (Run this to update!)
├── validate_setup.py       # 🕵️ Troubleshooting Tool (Checks env vars, imports, tokens)
├── bot.service             # ⚙️ Systemd Service configuration
├── requirements.txt        # 📦 Python Dependencies
├── .env                    # 🔑 Secrets (API Keys, Passwords, Config)
├── DEPLOYMENT_GUIDE.md     # ☁️ Specific Guide for GCP/Debian Environments
└── VALIDATION_PROTOCOL.md  # 🚑 Detailed Troubleshooting Steps
```

---

## ⚙️ Configuration

Create a `.env` file in the root directory. Use `.env.example` as a template.

### Essential Credentials
| Variable | Description |
| :--- | :--- |
| `SSH_PASSWORD_HUK` | Password for the Head Node. |
| `SSH_PASSWORD_BASTIAO` | Password for the Bastion Host. |
| `DISCORD_BOT_TOKEN` | Token from Discord Developer Portal. |
| `DISCORD_CHANNEL_ID` | Channel ID where alerts will be posted. |
| `GEMINI_API_KEY` | Google AI Studio Key for intelligent summaries. |

### Feature Flags
| Variable | Description | Default |
| :--- | :--- | :--- |
| `CHECK_INTERVAL` | How often (in seconds) to query the cluster. | `300` (5 mins) |
| `TIMEZONE_OFFSET` | UTC Offset for local timestamps (e.g., -5 for Peru). | `-5` |
| `TARGET_CLUSTER_USER` | The Slurm username to track for job alerts. | `carlos` |
| `DISCORD_USER_ID` | numerical Discord ID to ping when jobs finish. | (None) |

---

## 🚀 Installation & Usage

### 1. Initial Setup
```bash
# Clone the repo
git clone <repo-url>
cd server-notification

# Create Virtual Env (Recommended for Debian 12+)
python3 -m venv venv
source venv/bin/activate

# Install Dependencies
pip install -r requirements.txt
```

### 2. Update & Deploy
We use `deploy.py` for a hassle-free workflow. This script ensures you are in the venv, updates dependencies, and restarts the systemd service.

```bash
git pull origin master
python3 deploy.py
```

### 3. Manual Run (Debugging)
To run the bot in the foreground to see logs directly:
```bash
python3 monitor.py
```

### 4. Troubleshooting
If the bot fails to start or ssh connections fail:
```bash
python3 validate_setup.py
```
This script checks your `.env` integrity and module availability. See `VALIDATION_PROTOCOL.md` for more.

---

## 💬 Bot Command Reference

| Command | Usage | Description |
| :--- | :--- | :--- |
| **!status** | `!status` | Shows a visual traffic-light map (🟢🟡🔴) of all cluster partitions. |
| **!inspect** | `!inspect huk120` | SSHs into `huk120`, runs `free -m`, and shows exact CPU/RAM usage. |
| **!queue** | `!queue` | Lists total active jobs and a leaderboard of top users. |

---

## ⚡ Future: Agentic Integration (Claude/MCP Skill)

This project is architected to evolve into a **Model Context Protocol (MCP) Server** or a **Claude Custom Skill**. By exposing the internal `SlurmClient` methods, external AI Agents can autonomously manage computational workloads.

### How it works:
1.  **Resource Discovery**: The bot (acting as a Tool) exposes `get_node_states()` to an Agent.
2.  **Decision Making**: The Agent reads the JSON output (e.g., `{"huk120": {"state": "idle", "ram_free": 128}}`).
3.  **Autonomous Scheduling**: The Agent decides *"Node huk120 is free and meets the memory requirement"* and triggers a job submission command.

This transforms the bot from a passive **Monitor** into an active **Scheduler**, allowing for fully autonomous HPC workload management.
