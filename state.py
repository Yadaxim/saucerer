"""Per-chat conversation state for concurrent modes (clarification, edit, reminders, etc.)."""

from __future__ import annotations

from typing import Any

# chat_id -> state dict
_states: dict[int, dict[str, Any]] = {}


def new_idle_state() -> dict[str, Any]:
    return {"mode": "idle"}


def get_state(chat_id: int) -> dict[str, Any]:
    if chat_id not in _states:
        _states[chat_id] = new_idle_state()
    return _states[chat_id]


def reset_state(chat_id: int) -> None:
    st = get_state(chat_id)
    if st.get("mode") == MODE_AWAITING_MORNING_REPLY:
        _cancel_morning_nudges_if_loaded(chat_id)
    _states[chat_id] = new_idle_state()


def set_mode(chat_id: int, mode: str, **extra: Any) -> None:
    st = get_state(chat_id)
    old_mode = st.get("mode")
    if old_mode == MODE_AWAITING_MORNING_REPLY and mode != MODE_AWAITING_MORNING_REPLY:
        _cancel_morning_nudges_if_loaded(chat_id)
    st.clear()
    st["mode"] = mode
    for k, v in extra.items():
        st[k] = v


def _cancel_morning_nudges_if_loaded(chat_id: int) -> None:
    try:
        import reminders

        reminders.cancel_morning_nudges_for_chat(chat_id)
    except Exception:
        pass


def merge_state(chat_id: int, **extra: Any) -> None:
    st = get_state(chat_id)
    st.update(extra)


MODE_IDLE = "idle"
MODE_AWAITING_RECIPE = "awaiting_recipe"
MODE_AWAITING_CLARIFICATION = "awaiting_clarification"
MODE_AWAITING_INGREDIENT = "awaiting_ingredient"
MODE_AWAITING_MORNING_REPLY = "awaiting_morning_reply"
MODE_AWAITING_EDIT_PICK = "awaiting_edit_pick"
MODE_AWAITING_EDIT_INSTRUCTION = "awaiting_edit_instruction"
MODE_AWAITING_RECIPE_DISPLAY = "awaiting_recipe_display"

KEY_CLAR_TITLE = "clar_title"
KEY_CLAR_CONTENT = "clar_content"
KEY_CLAR_QA = "clar_qa"
KEY_CLAR_LAST_QUESTION = "clar_last_question"

KEY_EDIT_PATH = "edit_path"
KEY_EDIT_TITLE = "edit_title"
KEY_EDIT_CONTENT = "edit_content"

KEY_MORNING_FLOW_DATE = "morning_flow_date"
