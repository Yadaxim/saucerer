"""Append-only meal journal at RECIPES_DIR/journal.md."""

from __future__ import annotations

import logging
from datetime import datetime

from config import JOURNAL_PATH, RECIPES_DIR

logger = logging.getLogger(__name__)


def _ensure_parent() -> None:
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)


def append_journal_line(dish_name: str) -> None:
    """Append one line: ISO timestamp and dish name (no recipe body)."""
    _ensure_parent()
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} — {dish_name.strip()}\n"
    with open(JOURNAL_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    logger.info("Journal append: %s", dish_name.strip())


def read_journal_tail(max_lines: int = 30) -> str:
    if not JOURNAL_PATH.is_file():
        return "_No journal entries yet._"
    try:
        with open(JOURNAL_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        logger.error("Journal read failed: %s", e)
        return "_Could not read journal._"
    tail = lines[-max_lines:] if len(lines) > max_lines else lines
    body = "".join(tail).strip()
    return body if body else "_Journal is empty._"
