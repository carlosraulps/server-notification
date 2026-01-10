import sys
import os
import subprocess
import time

def run_cmd(command, exit_on_fail=True):
    """Runs a shell command."""
    print(f"🚀 Running: {command}")
    ret = subprocess.call(command, shell=True)
    if ret != 0:
        print(f"❌ Command failed: {command}")
        if exit_on_fail:
            sys.exit(ret)
    else:
        print("✅ Success")

def main():
    print("=== Slurm Bot Auto-Deployer ===\n")

    # 1. Check Venv
    if sys.prefix == sys.base_prefix:
        print("⚠️  WARNING: You do not seem to be running inside the Virtual Environment.")
        print("    If the install fails, please run: source venv/bin/activate")
        # Proceeding anyway usually works if they use `python3 deploy.py` inside the activated shell, 
        # but if they use /usr/bin/python3 it might fail permissions.
    
    # 2. Install Dependencies
    print("\n📦 Updating Dependencies...")
    run_cmd("pip install -r requirements.txt")

    # 3. Validate Setup
    print("\n🔍 Validating Environment...")
    run_cmd("python validate_setup.py")

    # 4. Update Systemd Service
    print("\n⚙️  Updating Systemd Service (Sudo required)...")
    run_cmd("sudo cp bot.service /etc/systemd/system/bot.service")
    run_cmd("sudo systemctl daemon-reload")
    run_cmd("sudo systemctl enable bot")
    
    print("\n🔄 Restarting Service...")
    run_cmd("sudo systemctl restart bot")

    print("\n✅ Deployment Complete! The bot should be running.")
    print("📜 Tailing logs now... (Press Ctrl+C to exit log view)")
    time.sleep(1)
    
    # 5. Tail Logs
    try:
        subprocess.run("journalctl -u bot -f", shell=True)
    except KeyboardInterrupt:
        print("\n👋 Log view exited. Bot is still running in background.")

if __name__ == "__main__":
    main()
