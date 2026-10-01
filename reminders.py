"""Scheduler: daily morning callback + one-off reminders via schedule + pending queue."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import date, datetime, time as time_of_day, timedelta

import schedule

import state as chat_state
from config import (
    MORNING_GIVEUP_HOUR,
    MORNING_GIVEUP_MINUTE,
    MORNING_NUDGE_INTERVAL_MINUTES,
    SCHEDULER_POLL_SECONDS,
)

logger = logging.getLogger(__name__)

_app_ref = None
_main_loop: asyncio.AbstractEventLoop | None = None
_pending_reminders: list[dict] = []  # {when: datetime, chat_id: int, text: str}
_morning_nudges: list[dict] = []  # {when: datetime, chat_id: int, flow_date: str ISO date}
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


def enqueue_morning_nudge(chat_id: int, run_at: datetime, flow_date_iso: str) -> None:
    _morning_nudges.append(
        {"when": run_at, "chat_id": chat_id, "flow_date": flow_date_iso}
    )
    logger.info(
        "Queued morning nudge chat_id=%s at %s flow=%s",
        chat_id,
        run_at,
        flow_date_iso,
    )


def cancel_morning_nudges_for_chat(chat_id: int) -> None:
    global _morning_nudges
    before = len(_morning_nudges)
    _morning_nudges = [n for n in _morning_nudges if n["chat_id"] != chat_id]
    dropped = before - len(_morning_nudges)
    if dropped:
        logger.info("Cancelled %d morning nudge(s) for chat_id=%s", dropped, chat_id)


def schedule_next_morning_nudge(chat_id: int, flow_date_iso: str) -> None:
    """Schedule the next half-hourly prompt if before local give-up time (default 14:00)."""
    now = datetime.now()
    if now.date().isoformat() != flow_date_iso:
        return
    giveup = datetime.combine(
        now.date(),
        time_of_day(MORNING_GIVEUP_HOUR, MORNING_GIVEUP_MINUTE),
    )
    next_t = now + timedelta(minutes=MORNING_NUDGE_INTERVAL_MINUTES)
    if next_t < giveup:
        enqueue_morning_nudge(chat_id, next_t, flow_date_iso)


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

    global _morning_nudges
    due_n = [n for n in _morning_nudges if n["when"] <= now]
    _morning_nudges = [n for n in _morning_nudges if n["when"] > now]
    for n in due_n:
        cid = n["chat_id"]
        flow_iso = n["flow_date"]
        st = chat_state.get_state(cid)
        if st.get("mode") != chat_state.MODE_AWAITING_MORNING_REPLY:
            continue
        if st.get(chat_state.KEY_MORNING_FLOW_DATE) != flow_iso:
            continue
        if date.today().isoformat() != flow_iso:
            continue
        if now >= datetime.combine(
            now.date(),
            time_of_day(MORNING_GIVEUP_HOUR, MORNING_GIVEUP_MINUTE),
        ):
            continue

        async def send_nudge(chat_id=cid):
            try:
                await _app_ref.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "👋 Still there — what are you planning to cook today?\n\n"
                        "_(Reply with a dish, or say you are not cooking.)_"
                    ),
                    parse_mode="Markdown",
                    reply_markup=_morning_keyboard_placeholder(),
                )
            except Exception as e:
                logger.error("Morning nudge send failed: %s", e)

        try:
            asyncio.run_coroutine_threadsafe(send_nudge(), _main_loop)
        except Exception as e:
            logger.error("Morning nudge schedule failed: %s", e)

        schedule_next_morning_nudge(cid, flow_iso)


_morning_keyboard = None


def set_morning_reply_keyboard(markup) -> None:
    """Reply keyboard for morning prompts and nudges (set from bot at startup)."""
    global _morning_keyboard
    _morning_keyboard = markup


def _morning_keyboard_placeholder():
    return _morning_keyboard


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
