# Validation Protocol

Your bot is crashing (`status=1/FAILURE`). This usually means missing files or config.

## Step 1: Run the Validator

I have included a new script to check your setup.

1.  **Update Code**:
    ```bash
    git pull origin master
    ```
2.  **Run Validator**:
    ```bash
    # Activate venv if not active
    source venv/bin/activate
    
    # Run check
    python validate_setup.py
    ```

## Step 2: Common Fixes

### ❌ MISSING .env
You forgot to create the configuration file.
```bash
cp .env.example .env
nano .env
# Paste your DISCORD_BOT_TOKEN here!
```

### ❌ Module 'discord' NOT FOUND
You need to install the new dependencies.
```bash
pip install -r requirements.txt
```

### ❌ DISCORD_BOT_TOKEN is missing
You created `.env` but didn't save the token inside it. Edit it again.

## Step 3: Manual Test

Before starting the systemd service, run the bot manually to see if it connects:

```bash
python monitor.py
```
- **Success**: You see `Logged in as BotName`. (Press `Ctrl+C` to stop).
- **Failure**: You see an error message. Send it to me!

## Step 4: Start Service

Once `python monitor.py` works manually:

```bash
sudo systemctl restart bot
sudo systemctl status bot
```
