# 🍳 Cooking Assistant Bot — Setup Guide

## What this does
- Sends you a **daily morning message** asking what you'll cook
- **Saves recipes** you send it as `.md` files in `~/recipes/`
- **Suggests what to cook** using Claude + your saved recipes
- **Searches recipes by ingredient** via Claude

---

## Step 1 — Get your API keys

### Telegram Bot Token
1. Open Telegram and search for `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the token it gives you (looks like `123456:ABCdef...`)

### Anthropic API Key
1. Go to https://console.anthropic.com
2. Create an API key under "API Keys"

### Your Telegram Chat ID
1. Search for `@userinfobot` on Telegram and send `/start`
2. It will reply with your numeric Chat ID

---

## Step 2 — Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate

pip install -U pip
pip install -r requirements.txt
```

---

## Step 3 — Configure the bot

Edit `cooking_bot.py` and fill in the top section:

```python
TELEGRAM_TOKEN    = "your_telegram_token_here"
ANTHROPIC_API_KEY = "your_anthropic_key_here"
YOUR_CHAT_ID      = 123456789        # your numeric chat ID
MORNING_TIME      = "08:00"          # when to send the daily prompt
```

---

## Step 4 — Test it manually

```bash
python cooking_bot.py
```

Open Telegram, find your bot, and send `/start`. You should see the menu appear.

---

## Step 5 — Run automatically on startup (Linux systemd)

```bash
# Copy the bot files
mkdir -p ~/cooking_bot
cp cooking_bot.py ~/cooking_bot/
cp requirements.txt ~/cooking_bot/

# Create a venv inside the runtime folder
cd ~/cooking_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# Edit the service file — replace YOUR_USERNAME with your actual Linux username
nano cooking_bot.service

# In cooking_bot.service, make sure ExecStart uses the venv python, e.g.:
# ExecStart=/home/YOUR_USERNAME/cooking_bot/.venv/bin/python /home/YOUR_USERNAME/cooking_bot/cooking_bot.py

# Install and enable the service
sudo cp cooking_bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable cooking_bot
sudo systemctl start cooking_bot

# Check it's running
sudo systemctl status cooking_bot
```

To view logs at any time:
```bash
journalctl -u cooking_bot -f
```

---

## How to use the bot

| Button / Command | What it does |
|---|---|
| ➕ Save a recipe | Paste any recipe in plain text — Claude formats and saves it |
| 🍽 Suggest what to cook | Claude picks 2-3 options from your saved recipes |
| 🔍 Search by ingredient | Find recipes that use a specific ingredient |
| 📋 List my recipes | See all saved recipe names |
| `/start` | Show the menu again |

Recipes are saved to `~/recipes/` as `.md` files — plain text you can read, edit, or back up anytime.

---

## Troubleshooting

**Bot doesn't respond** → Check the token in the config and that the bot is running (`systemctl status cooking_bot`)

**Morning message not arriving** → Make sure `YOUR_CHAT_ID` is set correctly (must be an integer, not a string)

**Recipe not saving** → Make sure `~/recipes/` exists and is writable (`mkdir -p ~/recipes`)
