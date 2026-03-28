#!/usr/bin/env python3
"""Telegram cooking assistant — entry point, handlers, scheduler."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import claude_client
import journal
import recipes
import reminders
import state
from config import (
    ANTHROPIC_API_KEY,
    BASE_DIR,
    BOT_LOG_BACKUP_COUNT,
    BOT_LOG_MAX_BYTES,
    BOT_LOG_PATH,
    CLAUDE_MODEL,
    JOURNAL_TAIL_LINES,
    RECIPES_DIR,
    TELEGRAM_TOKEN,
    YOUR_CHAT_ID,
    MORNING_TIME,
)

logger = logging.getLogger(__name__)

TG_CHUNK = 4000

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🍽 Suggest what to cook"), KeyboardButton("🔍 Search by ingredient")],
        [KeyboardButton("📋 List my recipes"), KeyboardButton("➕ Save a recipe")],
        [KeyboardButton("📓 Journal"), KeyboardButton("✏️ Edit recipe")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def _setup_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
        handlers=[
            RotatingFileHandler(
                BOT_LOG_PATH,
                maxBytes=BOT_LOG_MAX_BYTES,
                backupCount=BOT_LOG_BACKUP_COUNT,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )
    for quiet in ("httpx", "httpcore"):
        logging.getLogger(quiet).setLevel(logging.WARNING)


async def _reply_chunks(
    message,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup=None,
    only_last_markup: bool = True,
):
    """Send long text in Telegram-sized chunks."""
    if not text:
        return
    n = len(text)
    if n <= TG_CHUNK:
        await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    start = 0
    part_idx = 0
    while start < n:
        end = min(start + TG_CHUNK, n)
        chunk = text[start:end]
        start = end
        part_idx += 1
        is_last = start >= n
        mk = reply_markup if (is_last or not only_last_markup) else None
        await message.reply_text(chunk, parse_mode=parse_mode, reply_markup=mk)


def send_morning_prompt() -> None:
    app, loop = reminders.get_app_loop()
    if loop is None or app is None or not YOUR_CHAT_ID:
        return

    async def _send():
        state.set_mode(YOUR_CHAT_ID, state.MODE_AWAITING_MORNING_REPLY)
        await app.bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=(
                "☀️ Good morning! What are you planning to cook today?\n\n"
                "_(Reply here, or pick an option below)_"
            ),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )

    try:
        asyncio.run_coroutine_threadsafe(_send(), loop)
    except Exception as e:
        logger.error("Morning prompt failed: %s", e)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    state.reset_state(cid)
    await update.message.reply_text(
        "👨‍🍳 *Cooking Assistant* is ready!\n\n"
        "I'll ask you every morning what you're cooking, "
        "save your recipes, and help you decide when you're stuck.\n\n"
        "Say *remind me … at …* anytime (natural language) for a one-off reminder.\n\n"
        "Use the buttons below or type a command anytime.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    state.reset_state(update.effective_chat.id)
    await update.message.reply_text("Cancelled. What next?", reply_markup=MAIN_KEYBOARD)


async def cmd_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    state.set_mode(update.effective_chat.id, state.MODE_AWAITING_RECIPE)
    await update.message.reply_text(
        "📝 Send me the recipe now — just paste it in plain text "
        "(name, ingredients, steps). I'll check for missing details, then save it."
    )


async def cmd_suggest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🤔 Let me look through your recipes…")
    body = await asyncio.to_thread(recipes.load_all_recipes_text)
    reply = await asyncio.to_thread(claude_client.claude_suggest, body)
    await update.message.reply_text(reply, reply_markup=MAIN_KEYBOARD)


async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    state.set_mode(update.effective_chat.id, state.MODE_AWAITING_INGREDIENT)
    await update.message.reply_text("🔍 Which ingredient do you want to search for?")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    files = await asyncio.to_thread(recipes.list_recipes)
    logger.info("cmd_list: chat_id=%s count=%d", cid, len(files))
    if not files:
        await update.message.reply_text(
            "You have no saved recipes yet. Use ➕ Save a recipe to add your first one!",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    names = "\n".join(f"• {recipes.stem_display_name(f)}" for f in files)
    await update.message.reply_text(
        f"📋 *Your saved recipes ({len(files)}):*\n\n{names}",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_journal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    tail = await asyncio.to_thread(journal.read_journal_tail, JOURNAL_TAIL_LINES)
    await update.message.reply_text(
        f"📓 *Recent journal*\n\n{tail}",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    files = await asyncio.to_thread(recipes.list_recipes)
    if not files:
        await update.message.reply_text(
            "No saved recipes yet. Save one first with ➕ Save a recipe.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    names = "\n".join(f"• {recipes.stem_display_name(f)}" for f in files)
    state.set_mode(cid, state.MODE_AWAITING_EDIT_PICK)
    await update.message.reply_text(
        "✏️ *Which recipe should we edit?*\n\n"
        f"{names}\n\n"
        "Reply with the recipe name (or part of it).",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


def _looks_like_reminder(text: str) -> bool:
    return bool(re.search(r"\bremind\b", text.lower()))


async def _handle_reminder_natural_language(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str
) -> bool:
    """Parse and queue reminder; returns True if handled."""
    cid = update.effective_chat.id
    now_hint = datetime.now().strftime("%Y-%m-%d %H:%M %A")
    try:
        data = await asyncio.to_thread(claude_client.claude_parse_reminder, text, now_hint)
        rt = data.get("reminder_text", "").strip()
        th = data.get("time_24h", "").strip()
        day = data.get("day", "today").strip()
        if not rt or not th:
            await update.message.reply_text(
                "I couldn't work out a time or reminder text. Try e.g. "
                '*remind me to put the meat in at 1pm*.',
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return True
        run_at = await asyncio.to_thread(reminders.parse_reminder_datetime, day, th)
        await asyncio.to_thread(reminders.enqueue_reminder, cid, run_at, rt)
        await update.message.reply_text(
            f"⏰ I'll remind you at *{run_at.strftime('%Y-%m-%d %H:%M')}*:\n_{rt}_",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as e:
        logger.error("Reminder parse failed: %s", e)
        await update.message.reply_text(
            "Couldn't schedule that reminder. Try rephrasing with a clear time.",
            reply_markup=MAIN_KEYBOARD,
        )
    return True


async def _handle_morning_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    cid = update.effective_chat.id
    dish = text.strip()

    path = await asyncio.to_thread(recipes.find_recipe_path_by_dish_name, dish)
    if path is None:
        names = [recipes.stem_display_name(f) for f in await asyncio.to_thread(recipes.list_recipes)]
        if names:
            try:
                m = await asyncio.to_thread(claude_client.claude_match_morning_dish, dish, names)
                if m.get("match") and m.get("recipe_name"):
                    path = await asyncio.to_thread(recipes.path_for_display_name, m["recipe_name"])
            except Exception as e:
                logger.error("Morning Claude match failed: %s", e)

    await asyncio.to_thread(journal.append_journal_line, dish)

    if path:
        content = await asyncio.to_thread(recipes.read_recipe_file, path)
        summary = await asyncio.to_thread(claude_client.claude_summarize_recipe_brief, content)
        title = recipes.stem_display_name(path)
        await update.message.reply_text(
            f"✅ I've got *{title}* saved. Here's the gist:\n\n{summary}",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await update.message.reply_text(
            f"🍽 *{dish}* — I don't have that recipe saved yet.\n\n"
            "Want to add it? Tap ➕ *Save a recipe* and paste the details.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )

    state.reset_state(cid)


async def _finalize_save(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    title: str,
    content: str,
) -> None:
    cid = update.effective_chat.id
    path = await asyncio.to_thread(recipes.save_recipe, title, content)
    await asyncio.to_thread(journal.append_journal_line, title)
    state.reset_state(cid)
    await update.message.reply_text(
        f"✅ Saved *{title}* to `{path}`",
        parse_mode="Markdown",
    )
    full_md = await asyncio.to_thread(recipes.read_recipe_file, path)
    await _reply_chunks(update.message, full_md, reply_markup=MAIN_KEYBOARD)
    state.set_mode(
        cid,
        state.MODE_AWAITING_EDIT_INSTRUCTION,
        edit_path=str(path),
        edit_title=title,
        edit_content=full_md,
    )
    await update.message.reply_text(
        "✏️ Reply with a change in plain English (e.g. *double the garlic*) "
        "or use /edit another time. /cancel clears edit mode.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def _start_clarification(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    title: str,
    content: str,
) -> None:
    cid = update.effective_chat.id
    try:
        step = await asyncio.to_thread(
            claude_client.claude_recipe_clarification_step, title, content, []
        )
    except Exception as e:
        logger.error("Clarification step failed: %s", e)
        await _finalize_save(update, ctx, title, content)
        return

    if step.get("action") == "ready":
        t = step.get("title", title)
        c = step.get("content", content)
        await _finalize_save(update, ctx, t, c)
        return

    if step.get("action") == "ask" and step.get("question"):
        q = step["question"].strip()
        state.set_mode(
            cid,
            state.MODE_AWAITING_CLARIFICATION,
            clar_title=title,
            clar_content=content,
            clar_qa=[],
            clar_last_question=q,
        )
        await update.message.reply_text(
            f"🤔 Before I save:\n{q}",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await _finalize_save(update, ctx, title, content)


async def _continue_clarification(update: Update, ctx: ContextTypes.DEFAULT_TYPE, answer: str) -> None:
    cid = update.effective_chat.id
    st = state.get_state(cid)
    title = st.get("clar_title", "")
    content = st.get("clar_content", "")
    qa: list = list(st.get("clar_qa", []))
    last_q = st.get("clar_last_question", "")
    if last_q:
        qa.append((last_q, answer.strip()))

    try:
        step = await asyncio.to_thread(
            claude_client.claude_recipe_clarification_step, title, content, qa
        )
    except Exception as e:
        logger.error("Clarification continue failed: %s", e)
        state.reset_state(cid)
        await update.message.reply_text(
            "Something went wrong. Try /save again.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    if step.get("action") == "ready":
        t = step.get("title", title)
        c = step.get("content", content)
        await _finalize_save(update, ctx, t, c)
        return

    if step.get("action") == "ask" and step.get("question"):
        q = step["question"].strip()
        state.merge_state(
            cid,
            clar_title=title,
            clar_content=content,
            clar_qa=qa,
            clar_last_question=q,
        )
        await update.message.reply_text(q, reply_markup=MAIN_KEYBOARD)
        return

    t = step.get("title", title)
    c = step.get("content", content)
    await _finalize_save(update, ctx, t, c)


async def _apply_edit_and_save(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    instruction: str,
) -> None:
    cid = update.effective_chat.id
    st = state.get_state(cid)
    path_s = st.get("edit_path")
    if not path_s:
        state.reset_state(cid)
        await update.message.reply_text("Nothing to edit. Try /edit.", reply_markup=MAIN_KEYBOARD)
        return
    path = Path(path_s)
    full_md = st.get("edit_content") or await asyncio.to_thread(recipes.read_recipe_file, path)
    try:
        new_md = await asyncio.to_thread(
            claude_client.claude_apply_recipe_edit, full_md, instruction
        )
    except Exception as e:
        logger.error("Edit failed: %s", e)
        await update.message.reply_text(
            "Couldn't apply that edit. Try rephrasing.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    await asyncio.to_thread(recipes.write_full_markdown, path, new_md)
    state.merge_state(cid, edit_content=new_md)
    await update.message.reply_text("✅ Updated and saved.", reply_markup=MAIN_KEYBOARD)
    await _reply_chunks(update.message, new_md, reply_markup=MAIN_KEYBOARD)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    text = (update.message.text or "").strip()
    st = state.get_state(cid)
    mode = st.get("mode", state.MODE_IDLE)

    if text == "🍽 Suggest what to cook":
        return await cmd_suggest(update, ctx)
    if text == "🔍 Search by ingredient":
        return await cmd_search(update, ctx)
    if text == "📋 List my recipes":
        return await cmd_list(update, ctx)
    if text == "➕ Save a recipe":
        return await cmd_save(update, ctx)
    if text == "📓 Journal":
        return await cmd_journal(update, ctx)
    if text == "✏️ Edit recipe":
        return await cmd_edit(update, ctx)

    if mode == state.MODE_AWAITING_RECIPE:
        await update.message.reply_text("⏳ Parsing your recipe…")
        try:
            parsed = await asyncio.to_thread(claude_client.claude_parse_recipe, text)
            title = parsed.get("title", "Untitled")
            content = parsed.get("content", "")
        except Exception as e:
            logger.error("Recipe parse error: %s", e)
            state.reset_state(cid)
            await update.message.reply_text(
                "⚠️ Couldn't parse that as a recipe. Try again with more detail.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        await update.message.reply_text("🔎 Checking for anything unclear…")
        await _start_clarification(update, ctx, title, content)
        return

    if mode == state.MODE_AWAITING_CLARIFICATION:
        await _continue_clarification(update, ctx, text)
        return

    if mode == state.MODE_AWAITING_INGREDIENT:
        await update.message.reply_text(f"🔍 Searching for recipes with *{text}*…", parse_mode="Markdown")
        matches = await asyncio.to_thread(recipes.search_recipes_by_ingredient, text)
        reply = await asyncio.to_thread(claude_client.claude_search, text, matches)
        state.reset_state(cid)
        await update.message.reply_text(reply, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
        return

    if mode == state.MODE_AWAITING_MORNING_REPLY:
        await _handle_morning_reply(update, ctx, text)
        return

    if mode == state.MODE_AWAITING_EDIT_PICK:
        path = await asyncio.to_thread(recipes.resolve_recipe_by_user_pick, text)
        if path is None:
            await update.message.reply_text(
                "I couldn't match that to one recipe. Pick from the list or type a clearer name.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        full_md = await asyncio.to_thread(recipes.read_recipe_file, path)
        title = recipes.stem_display_name(path)
        state.set_mode(
            cid,
            state.MODE_AWAITING_EDIT_INSTRUCTION,
            edit_path=str(path),
            edit_title=title,
            edit_content=full_md,
        )
        await update.message.reply_text(
            f"✏️ Editing *{title}*. Here's the recipe; reply with what to change.",
            parse_mode="Markdown",
        )
        await _reply_chunks(update.message, full_md, reply_markup=MAIN_KEYBOARD)
        return

    if mode == state.MODE_AWAITING_EDIT_INSTRUCTION:
        await _apply_edit_and_save(update, ctx, text)
        return

    if mode == state.MODE_IDLE and _looks_like_reminder(text):
        return await _handle_reminder_natural_language(update, ctx, text)

    await update.message.reply_text(
        "Use the buttons below, or try:\n"
        "/suggest  /save  /search  /list  /journal  /edit  /cancel\n\n"
        "You can also say *remind me to … at …* in natural language.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def post_init(app: Application) -> None:
    loop = asyncio.get_running_loop()
    reminders.set_app_and_loop(app, loop)
    logger.info(
        "Startup paths: base_dir=%s recipes_dir=%s model=%s",
        BASE_DIR,
        RECIPES_DIR,
        CLAUDE_MODEL,
    )


def main() -> None:
    _setup_logging()
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)

    if not TELEGRAM_TOKEN or not ANTHROPIC_API_KEY:
        raise SystemExit(
            "Set TELEGRAM_TOKEN and ANTHROPIC_API_KEY in a .env file (see .env.example)."
        )

    reminders.configure_daily_morning(MORNING_TIME, send_morning_prompt)
    reminders.start_scheduler_thread()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("suggest", cmd_suggest))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("journal", cmd_journal))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Cooking bot started. Morning prompt at %s.", MORNING_TIME)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
