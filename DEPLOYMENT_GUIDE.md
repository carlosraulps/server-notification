# Deployment Guide: Slurm Monitor on Google Cloud Free Tier

This guide will help you run your `monitor.py` script 24/7 on a free Google Cloud Compute Engine instance.

## 1. Create the VPS (Virtual Machine)

1. Go to [Google Cloud Console](https://console.cloud.google.com/).
2. Navigate to **Compute Engine** > **VM instances**.
3. Click **Create Instance**.
4. **Configuration for Free Tier**:
    *   **Name**: `slurm-monitor` (or anything you like).
    *   **Region**: `us-west1`, `us-central1`, or `us-east1`. (Important!)
    *   **Zone**: Any in the region (e.g., `us-west1-b`).
    *   **Machine configuration**:
        *   Series: `E2`
        *   Machine type: `e2-micro` (2 vCPU, 1 GB memory).
    *   **Boot Disk**:
        *   Click "Change".
        *   Operating System: **Ubuntu**.
        *   Version: **Ubuntu 22.04 LTS**.
        *   Boot disk type: **Standard persistent disk**.
        *   Size: **30 GB**. (Free tier allows up to 30GB).
5. **Firewall**: No special ports needed (outbound traffic is allowed by default).
6. Click **Create**.

## 2. Connect to the Server

1. Once the instance is running, click the **SSH** button in the dashboard to open a web-based terminal.
2. Alternatively, use your local terminal if you added your SSH key (recommended).

## 3. Setup the Environment

Run the following commands on the server (copy-paste):

```bash
# Update package list and install Python pip
sudo apt update && sudo apt install python3-pip -y

# Create a directory
mkdir ~/server-notification
cd ~/server-notification
```

## 4. Upload Files

You have two options to get your files onto the server:

### Option A: Clone from Git (If you pushed this code to GitHub)
```bash
git clone https://github.com/your-username/your-repo.git .
```

### Option B: Copy-Paste (Simplest for invalid/no repo)
1. **monitor.py**:
   ```bash
   nano monitor.py
   # Paste the content of monitor.py here.
   # Press Ctrl+X, then Y, then Enter to save.
   ```
2. **requirements.txt**:
   ```bash
   nano requirements.txt
   # Paste content:
   # fabric
   # requests
   # python-dotenv
   ```
3. **.env** (Crucial!):
   ```bash
   nano .env
   # Paste your ACTUAL credentials here.
   ```

## 5. Install Dependencies

```bash
pip3 install -r requirements.txt
```

## 6. Run in Background (Systemd)

To make sure the bot runs 24/7 and restarts on reboot:

1. **Create the service file**:
   ```bash
   sudo nano /etc/systemd/system/bot.service
   ```

2. **Paste the following content**:
   (Make sure the paths match. If you are user `ubuntu`, `/home/ubuntu` is correct).
   ```ini
   [Unit]
   Description=Slurm Cluster Monitor Bot
   After=network.target

   [Service]
   User=ubuntu
   WorkingDirectory=/home/ubuntu/server-notification
   ExecStart=/usr/bin/python3 /home/ubuntu/server-notification/monitor.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

3. **Enable and Start**:
   ```bash
   # Reload systemd
   sudo systemctl daemon-reload

   # Enable service to start on boot
   sudo systemctl enable bot

   # Start the service now
   sudo systemctl start bot
   ```

4. **Check Status**:
   ```bash
   sudo systemctl status bot
   ```
   You should see `Active: active (running)`.

5. **View Logs**:
   To see the output (print statements):
   ```bash
   journalctl -u bot -f
   ```

## Done!
Your bot is now watching HUK 24/7.
