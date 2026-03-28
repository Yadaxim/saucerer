"""Scheduler: daily morning callback + one-off reminders via schedule + pending queue."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, timedelta

import schedule

from config import SCHEDULER_POLL_SECONDS

logger = logging.getLogger(__name__)

_app_ref = None
_main_loop: asyncio.AbstractEventLoop | None = None
_pending_reminders: list[dict] = []  # {when: datetime, chat_id: int, text: str}
_morning_job = None
_morning_time_str = "10:00"


def set_app_and_loop(app, loop: asyncio.AbstractEventLoop | None) -> None:
    global _app_ref, _main_loop
    _app_ref = app
    _main_loop = loop


def get_app_loop():
    return _app_ref, _main_loop


def capture_loop() -> None:
    global _main_loop
    _main_loop = asyncio.get_running_loop()


def enqueue_reminder(chat_id: int, run_at: datetime, reminder_text: str) -> None:
    """Fire one Telegram message when local time reaches run_at (naive datetime)."""
    _pending_reminders.append(
        {"when": run_at, "chat_id": chat_id, "text": reminder_text.strip()}
    )
    logger.info("Queued reminder chat_id=%s at %s", chat_id, run_at)


def _tick_pending_reminders() -> None:
    if _main_loop is None or _app_ref is None:
        return
    now = datetime.now()
    global _pending_reminders
    due = [r for r in _pending_reminders if r["when"] <= now]
    _pending_reminders = [r for r in _pending_reminders if r["when"] > now]
    for r in due:

        async def send(cid=r["chat_id"], txt=r["text"]):
            try:
                await _app_ref.bot.send_message(chat_id=cid, text=f"⏰ Reminder: {txt}")
            except Exception as e:
                logger.error("Reminder send failed: %s", e)

        try:
            asyncio.run_coroutine_threadsafe(send(), _main_loop)
        except Exception as e:
            logger.error("Reminder schedule send failed: %s", e)


def parse_reminder_datetime(day: str, time_24h: str, now: datetime | None = None) -> datetime:
    """Build local naive datetime from day keyword or YYYY-MM-DD and HH:MM."""
    now = now or datetime.now()
    t_parts = time_24h.strip().split(":")
    hour = int(t_parts[0])
    minute = int(t_parts[1]) if len(t_parts) > 1 else 0
    day_norm = day.strip().lower()
    if day_norm == "today":
        d = now.date()
    elif day_norm == "tomorrow":
        d = now.date() + timedelta(days=1)
    else:
        d = date.fromisoformat(day)
    target = datetime(d.year, d.month, d.day, hour, minute, 0)
    if day_norm == "today" and target <= now:
        target += timedelta(days=1)
    return target


def configure_daily_morning(morning_time: str, job_fn) -> None:
    global _morning_time_str, _morning_job
    _morning_time_str = morning_time.strip()
    _morning_job = job_fn


def run_scheduler_loop() -> None:
    schedule.every(1).minutes.do(_tick_pending_reminders)
    if _morning_job:
        schedule.every().day.at(_morning_time_str).do(_morning_job)
    while True:
        schedule.run_pending()
        time.sleep(SCHEDULER_POLL_SECONDS)


def start_scheduler_thread() -> threading.Thread:
    t = threading.Thread(target=run_scheduler_loop, daemon=True)
    t.start()
    return t
