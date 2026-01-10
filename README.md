# Slurm Cluster Monitor & Discord Bot

A hybrid Discord Bot that monitors a Slurm-managed HPC cluster. It sends alerts when nodes become free and responds to commands for detailed cluster status.

## Features

- **Real-time Alerts**: Automatically notifies a specific channel when nodes transition from Busy to Idle.
- **Detailed Inspection**: Interactive commands to check exact RAM/CPU usage of specific nodes (fixing common Slurm reporting bugs).
- **Cluster Visualization**: `!status` command provides a clean dashboard of node states.
- **SSH ProxyJump**: Seamlessly connects through bastion hosts.

## Commands

- `!status`: Show a visual map of all partitions and node states.
- `!inspect <node_name>`: Get detailed specs (RealMemory, AllocMem, CPULoad) for a specific node.
- `!queue`: Summary of active jobs and top users.

## Setup

1.  **Discord Bot Setup**:
    - Go to [Discord Developer Portal](https://discord.com/developers/applications).
    - Create an Application and add a Bot.
    - **Enable "Message Content Intent"** (Privileged Gateway Intents).
    - Copy the **Bot Token**.
    - Invite the bot to your server.

2.  **Installation**:
    ```bash
    # Create venv (Debian 12 compatible)
    python3 -m venv venv
    source venv/bin/activate
    
    # Install
    pip install -r requirements.txt
    ```

3.  **Configuration**:
    Copy `.env.example` to `.env`:
    ```bash
    cp .env.example .env
    ```
    Fill in `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, and your SSH credentials.

4.  **Run**:
    ```bash
    python monitor.py
    ```

## Deployment (Quick Start)

If you are deploying this for the first time or updating on your VPS, follow these commands:

### 1. Update Code on VPS
```bash
git pull origin master
```

### 2. Update Linux Service (If files changed)
```bash
# Copy the service file to systemd
sudo cp bot.service /etc/systemd/system/bot.service

# Reload and restart
sudo systemctl daemon-reload
sudo systemctl enable bot
sudo systemctl restart bot
```

### 3. Check Logs
```bash
journalctl -u bot -f
```

For a detailed guide on setting up the Google Cloud environment, see [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md).
