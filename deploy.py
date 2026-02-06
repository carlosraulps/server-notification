import os
import subprocess
import sys

def run_command(command):
    """Runs a shell command and prints output."""
    print(f"🔹 Running: {command}")
    result = subprocess.run(command, shell=True, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"❌ Error:\n{result.stderr}")
        return False
    print(f"✅ Success:\n{result.stdout}")
    return True

def main():
    print("🚀 Starting Deployment for Slurm Bot (Modular Refactor)...")
    
    # 1. Pull Latest Code
    if not run_command("git pull origin master"):
        print("⚠️ Git pull failed. Continuing anyway...")

    # 2. Check Directories
    dirs = ["cogs", "utils", "data"]
    for d in dirs:
        if not os.path.exists(d):
            os.makedirs(d)
            print(f"📁 Created directory: {d}")

    # 3. Update Dependencies
    pip_cmd = "venv/bin/pip" if os.path.exists("venv") else "pip3"
    if not run_command(f"{pip_cmd} install -r requirements.txt"):
        print("❌ Failed to install dependencies.")
        sys.exit(1)

    # 4. Update Systemd Service
    # Copy new service file (points to bot_entry.py)
    if not run_command("sudo cp bot.service /etc/systemd/system/bot.service"):
        print("❌ Failed to copy service file.")
        
    if not run_command("sudo systemctl daemon-reload"):
        print("❌ Failed to reload daemon.")

    # 5. Restart Service
    if not run_command("sudo systemctl restart bot.service"):
        print("❌ Failed to restart service.")
        sys.exit(1)

    print("🎉 Deployment Complete! The bot is running via bot_entry.py.")
    print("logs: journalctl -u bot.service -f")

if __name__ == "__main__":
    main()
