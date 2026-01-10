import os
import sys

def check_file(filename):
    if os.path.exists(filename):
        print(f"✅ Found {filename}")
        return True
    else:
        print(f"❌ MISSING {filename}")
        return False

def check_import(module_name):
    try:
        __import__(module_name)
        print(f"✅ Module '{module_name}' installed")
        return True
    except ImportError:
        print(f"❌ Module '{module_name}' NOT FOUND. Run: pip install -r requirements.txt")
        return False

def check_env_vars():
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        token = os.getenv("DISCORD_BOT_TOKEN")
        channel = os.getenv("DISCORD_CHANNEL_ID")
        
        if not token or token == "your_bot_token_here":
            print("❌ DISCORD_BOT_TOKEN is missing or default in .env")
            return False
        else:
            print("✅ DISCORD_BOT_TOKEN found")

        if not channel:
            print("⚠️ DISCORD_CHANNEL_ID is missing (Bot will run but alerts won't work)")
        else:
            print("✅ DISCORD_CHANNEL_ID found")
            
        return True
    except ImportError:
        print("❌ Could not import dotenv (python-dotenv missing)")
        return False

def main():
    print("=== Slurm Bot Setup Validator ===\n")
    
    # 1. Check Files
    checks = [
        check_file(".env"),
        check_file("monitor.py"),
        check_file("requirements.txt")
    ]
    
    # 2. Check Dependencies
    print("\nChecking Imports...")
    checks.append(check_import("discord"))
    checks.append(check_import("fabric"))
    checks.append(check_import("dotenv"))
    
    # 3. Check Config
    if check_import("dotenv"):
        print("\nChecking Configuration...")
        checks.append(check_env_vars())
    
    print("\n" + "="*30)
    if all(checks):
        print("🎉 SUCCESS: Environment looks good! You can run 'python monitor.py'")
    else:
        print("🛑 FAILURE: Please fix the errors above.")
        sys.exit(1)

if __name__ == "__main__":
    main()
