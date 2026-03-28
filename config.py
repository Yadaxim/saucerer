"""Configuration: env, paths, times, logging defaults."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
_cid = os.getenv("TELEGRAM_CHAT_ID", "").strip()
YOUR_CHAT_ID = int(_cid) if _cid else 0

RECIPES_DIR = Path(os.path.expanduser(os.getenv("RECIPES_DIR", "~/recipes"))).expanduser()
JOURNAL_PATH = RECIPES_DIR / "journal.md"

MORNING_TIME = os.getenv("MORNING_TIME", "10:00").strip()
CLAUDE_MODEL = "claude-sonnet-4-20250514"

_log_path = Path(os.getenv("BOT_LOG_FILE", str(BASE_DIR / "cooking_bot.log")))
_log_max_bytes = int(os.getenv("BOT_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
_log_backups = int(os.getenv("BOT_LOG_BACKUP_COUNT", "3"))

BOT_LOG_PATH = _log_path
BOT_LOG_MAX_BYTES = max(_log_max_bytes, 256 * 1024)
BOT_LOG_BACKUP_COUNT = max(_log_backups, 1)

JOURNAL_TAIL_LINES = int(os.getenv("JOURNAL_TAIL_LINES", "30"))

SCHEDULER_POLL_SECONDS = float(os.getenv("SCHEDULER_POLL_SECONDS", "30"))
