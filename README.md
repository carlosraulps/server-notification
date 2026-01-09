# Slurm Cluster Monitor & Discord Bot

A Python script that monitors a Slurm-managed HPC cluster for idle nodes and sends real-time notifications to a Discord channel. Designed for researchers who need to jump on free resources immediately.

## Features

- **Real-time Monitoring**: Checks `sinfo` for idle nodes in specified partitions (default: `alto`, `medio`, `normal`).
- **Smart Notifications**: Only alerts when a node *becomes* free (Busy -> Idle transition) to prevent spam.
- **Discord Integration**: Sends a rich Embed with node specs (CPUs, RAM) and current queue status.
- **SSH ProxyJump Support**: Handles connections via a bastion host seamlessly using `fabric`.

## Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    Copy `.env` and fill in your details:
    ```bash
    cp .env.example .env  # (If you created an example, otherwise just edit .env)
    # Edit .env with your SSH credentials and Discord Webhook URL
    ```

3.  **Run**:
    ```bash
    python monitor.py
    ```

## Deployment

See [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for instructions on running this 24/7 on Google Cloud Free Tier.
