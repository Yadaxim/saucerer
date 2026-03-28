#!/usr/bin/env python3
"""
Cooking Assistant Bot
- Daily morning prompt via Telegram
- Save recipes as local files
- Suggest what to cook using Claude
- Search recipes by ingredient
"""

import os
import re
import json
import logging
from logging.handlers import RotatingFileHandler
import asyncio
import schedule
import time
import threading
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from anthropic import Anthropic

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# ─────────────────────────────────────────────
# CONFIGURATION  ← copy .env.example → .env and fill in values
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
_cid = os.getenv("TELEGRAM_CHAT_ID", "").strip()
YOUR_CHAT_ID = int(_cid) if _cid else 0
RECIPES_DIR      = Path("/home/adair/recipes")
MORNING_TIME     = "10:00"    # 24h format, local time
# ─────────────────────────────────────────────

_log_path = Path(os.getenv("BOT_LOG_FILE", str(BASE_DIR / "cooking_bot.log")))
_log_max_bytes = int(os.getenv("BOT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
_log_backups = int(os.getenv("BOT_LOG_BACKUP_COUNT", "3"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        RotatingFileHandler(
            _log_path,
            maxBytes=max(_log_max_bytes, 256 * 1024),
            backupCount=max(_log_backups, 1),
            encoding="utf-8",
        ),
        logging.StreamHandler(),
    ],
)
for _quiet in ("httpx", "httpcore"):
    logging.getLogger(_quiet).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

RECIPES_DIR.mkdir(parents=True, exist_ok=True)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)
logger.info("Startup paths: base_dir=%s recipes_dir=%s", BASE_DIR, RECIPES_DIR)

# Conversation state per user
user_state: dict = {}   # chat_id → {"mode": str, "buffer": str}


# ─────────────────────────────────────────────
# RECIPE HELPERS
# ─────────────────────────────────────────────

def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "_", name)
    return name[:60]


def save_recipe(name: str, content: str) -> Path:
    filename = RECIPES_DIR / f"{slugify(name)}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\n")
        f.write(f"_Saved on {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
        f.write(content.strip())
    logger.info("Recipe saved: path=%s title=%s", filename, name)
    return filename


def list_recipes() -> list[Path]:
    files = sorted(RECIPES_DIR.glob("*.md"))
    logger.debug(
        "List recipes scan: dir=%s count=%d",
        RECIPES_DIR,
        len(files),
    )
    return files


def load_all_recipes() -> str:
    files = list_recipes()
    if not files:
        return ""
    parts = []
    for f in files:
        parts.append(f"### {f.stem.replace('_', ' ').title()}\n{f.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def search_recipes_by_ingredient(ingredient: str) -> list[tuple[str, str]]:
    results = []
    ingredient_lower = ingredient.lower()
    for f in list_recipes():
        content = f.read_text(encoding="utf-8")
        if ingredient_lower in content.lower():
            title = f.stem.replace("_", " ").title()
            results.append((title, content))
    return results


# ─────────────────────────────────────────────
# CLAUDE CALLS
# ─────────────────────────────────────────────

def claude_suggest(recipes_text: str) -> str:
    if not recipes_text:
        return ("You don't have any saved recipes yet! "
                "Send me a recipe using /save and I'll remember it for next time.")
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        system=(
            "You are a friendly home cooking assistant. "
            "Given a list of the user's saved recipes, suggest 2-3 great options "
            "for today based on variety and appeal. Be warm and concise."
        ),
        messages=[{
            "role": "user",
            "content": f"Here are my saved recipes:\n\n{recipes_text}\n\nWhat should I cook today?"
        }]
    )
    return response.content[0].text


def claude_search(ingredient: str, matches: list[tuple[str, str]]) -> str:
    if not matches:
        return f"I couldn't find any recipes containing **{ingredient}**. Try /save to add more recipes!"
    recipe_block = "\n\n".join(
        f"**{title}**\n{content[:400]}..." for title, content in matches
    )
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        system="You are a helpful cooking assistant. Summarise which saved recipes use the requested ingredient and briefly explain each one.",
        messages=[{
            "role": "user",
            "content": f"I searched for '{ingredient}' and found these recipes:\n\n{recipe_block}\n\nGive me a helpful summary."
        }]
    )
    return response.content[0].text


def claude_parse_recipe(raw: str) -> dict:
    """Ask Claude to extract a clean title + formatted recipe from freeform text."""
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        system=(
            "Extract a recipe from the user's message. "
            "Respond ONLY with a JSON object with two keys: "
            "'title' (short recipe name) and 'content' (well-formatted recipe with ingredients and steps). "
            "No markdown fences, no extra text."
        ),
        messages=[{"role": "user", "content": raw}]
    )
    raw_json = response.content[0].text.strip()
    raw_json = re.sub(r"^```json|^```|```$", "", raw_json, flags=re.MULTILINE).strip()
    return json.loads(raw_json)


# ─────────────────────────────────────────────
# KEYBOARD
# ─────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🍽 Suggest what to cook"), KeyboardButton("🔍 Search by ingredient")],
        [KeyboardButton("📋 List my recipes"),      KeyboardButton("➕ Save a recipe")],
    ],
    resize_keyboard=True,
    is_persistent=True
)


# ─────────────────────────────────────────────
# HANDLERS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = {"mode": "idle"}
    await update.message.reply_text(
        "👨‍🍳 *Cooking Assistant* is ready!\n\n"
        "I'll ask you every morning what you're cooking, "
        "save your recipes, and help you decide when you're stuck.\n\n"
        "Use the buttons below or type a command anytime.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )


async def cmd_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = {"mode": "awaiting_recipe"}
    await update.message.reply_text(
        "📝 Send me the recipe now — just paste it in plain text "
        "(name, ingredients, steps). I'll format and save it for you."
    )


async def cmd_suggest(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤔 Let me look through your recipes…")
    recipes = load_all_recipes()
    reply = await asyncio.to_thread(claude_suggest, recipes)
    await update.message.reply_text(reply, reply_markup=MAIN_KEYBOARD)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_state[chat_id] = {"mode": "awaiting_ingredient"}
    await update.message.reply_text("🔍 Which ingredient do you want to search for?")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    logger.info("cmd_list requested: chat_id=%s", chat_id)
    files = list_recipes()
    logger.info("cmd_list result: chat_id=%s count=%d", chat_id, len(files))
    if not files:
        await update.message.reply_text(
            "You have no saved recipes yet. Use ➕ Save a recipe to add your first one!",
            reply_markup=MAIN_KEYBOARD
        )
        return
    names = "\n".join(f"• {f.stem.replace('_', ' ').title()}" for f in files)
    await update.message.reply_text(
        f"📋 *Your saved recipes ({len(files)}):*\n\n{names}",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip()

    # Button shortcuts
    if text == "🍽 Suggest what to cook":
        return await cmd_suggest(update, ctx)
    if text == "🔍 Search by ingredient":
        return await cmd_search(update, ctx)
    if text == "📋 List my recipes":
        return await cmd_list(update, ctx)
    if text == "➕ Save a recipe":
        return await cmd_save(update, ctx)

    state = user_state.get(chat_id, {}).get("mode", "idle")

    # ── Saving a recipe ──
    if state == "awaiting_recipe":
        await update.message.reply_text("⏳ Parsing and saving your recipe…")
        try:
            parsed = await asyncio.to_thread(claude_parse_recipe, text)
            path = save_recipe(parsed["title"], parsed["content"])
            user_state[chat_id] = {"mode": "idle"}
            await update.message.reply_text(
                f"✅ Saved *{parsed['title']}* to `{path}`",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD
            )
        except Exception as e:
            logger.error("Recipe parse error: %s", e)
            await update.message.reply_text(
                "⚠️ Couldn't parse that as a recipe. Try again with more detail.",
                reply_markup=MAIN_KEYBOARD
            )
            user_state[chat_id] = {"mode": "idle"}
        return

    # ── Searching by ingredient ──
    if state == "awaiting_ingredient":
        await update.message.reply_text(f"🔍 Searching for recipes with *{text}*…", parse_mode="Markdown")
        matches = await asyncio.to_thread(search_recipes_by_ingredient, text)
        reply = await asyncio.to_thread(claude_search, text, matches)
        user_state[chat_id] = {"mode": "idle"}
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
        return

    # ── Morning prompt reply ──
    if state == "awaiting_morning_reply":
        await update.message.reply_text("Sounds delicious! Want me to save that recipe? Use ➕ Save a recipe.")
        user_state[chat_id] = {"mode": "idle"}
        return

    # ── Fallback ──
    await update.message.reply_text(
        "Use the buttons below, or try:\n/suggest  /save  /search  /list",
        reply_markup=MAIN_KEYBOARD
    )


# ─────────────────────────────────────────────
# MORNING PROMPT SCHEDULER
# ─────────────────────────────────────────────

_app_ref = None       # set after Application is built
_main_loop = None     # asyncio loop used by app.run_polling() (main thread only)


def send_morning_prompt():
    async def _send():
        if _app_ref and YOUR_CHAT_ID:
            user_state[YOUR_CHAT_ID] = {"mode": "awaiting_morning_reply"}
            await _app_ref.bot.send_message(
                chat_id=YOUR_CHAT_ID,
                text=f"☀️ Good morning! What are you planning to cook today?\n\n"
                     f"_(Reply here, or pick an option below)_",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD
            )
    if _main_loop is None:
        return
    asyncio.run_coroutine_threadsafe(_send(), _main_loop)


def run_scheduler():
    schedule.every().day.at(MORNING_TIME).do(send_morning_prompt)
    while True:
        schedule.run_pending()
        time.sleep(30)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

async def _capture_main_loop(app: Application):
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def main():
    global _app_ref

    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        raise SystemExit(
            "Set TELEGRAM_TOKEN and ANTHROPIC_API_KEY in a .env file (see .env.example)."
        )

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_capture_main_loop)
        .build()
    )
    _app_ref = app

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("save",    cmd_save))
    app.add_handler(CommandHandler("suggest", cmd_suggest))
    app.add_handler(CommandHandler("search",  cmd_search))
    app.add_handler(CommandHandler("list",    cmd_list))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start scheduler in background thread
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    logger.info("🤖 Cooking bot started. Morning prompt at %s.", MORNING_TIME)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
