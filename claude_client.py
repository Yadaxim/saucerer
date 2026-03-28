"""Anthropic API calls (sync); call via asyncio.to_thread from handlers."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from anthropic import Anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _strip_json_fence(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```json\s*|^```\s*|\s*```$", "", t, flags=re.MULTILINE).strip()
    return t


def claude_suggest(recipes_text: str) -> str:
    if not recipes_text:
        return (
            "You don't have any saved recipes yet! "
            "Send me a recipe using /save and I'll remember it for next time."
        )
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        system=(
            "You are a friendly home cooking assistant. "
            "Given a list of the user's saved recipes, suggest 2-3 great options "
            "for today based on variety and appeal. Be warm and concise."
        ),
        messages=[
            {
                "role": "user",
                "content": f"Here are my saved recipes:\n\n{recipes_text}\n\nWhat should I cook today?",
            }
        ],
    )
    return response.content[0].text


def claude_search(ingredient: str, matches: list[tuple[str, str]]) -> str:
    if not matches:
        return (
            f"I couldn't find any recipes containing **{ingredient}**. "
            "Try /save to add more recipes!"
        )
    recipe_block = "\n\n".join(
        f"**{title}**\n{content[:400]}..." for title, content in matches
    )
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=600,
        system=(
            "You are a helpful cooking assistant. Summarise which saved recipes use "
            "the requested ingredient and briefly explain each one."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"I searched for '{ingredient}' and found these recipes:\n\n"
                    f"{recipe_block}\n\nGive me a helpful summary."
                ),
            }
        ],
    )
    return response.content[0].text


def claude_parse_recipe(raw: str) -> dict[str, str]:
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        system=(
            "Extract a recipe from the user's message. "
            "Respond ONLY with a JSON object with two keys: "
            "'title' (short recipe name) and 'content' (well-formatted recipe with ingredients and steps). "
            "No markdown fences, no extra text."
        ),
        messages=[{"role": "user", "content": raw}],
    )
    raw_json = _strip_json_fence(response.content[0].text)
    return json.loads(raw_json)


def claude_recipe_clarification_step(
    title: str, content: str, qa_history: list[tuple[str, str]]
) -> dict[str, Any]:
    """
    Returns JSON:
    { "action": "ask", "question": "..." }  OR  { "action": "ready", "title": "...", "content": "..." }
    """
    qa_block = ""
    for q, a in qa_history:
        qa_block += f"Q: {q}\nA: {a}\n\n"
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=900,
        system=(
            "You help refine recipes before they are saved. "
            "Check the recipe for missing quantities, ambiguous steps, missing times/temps, or unclear wording. "
            "If clarification is still needed, ask ONE concrete question the user can answer in one message. "
            "If nothing important is missing or ambiguous anymore, mark ready to save. "
            "Respond ONLY with JSON: either "
            '{"action":"ask","question":"<single question>"} or '
            '{"action":"ready","title":"<string>","content":"<full markdown recipe body>"} '
            "Use the same language as the recipe. No markdown fences."
        ),
        messages=[
            {
                "role": "user",
                "content": (
                    f"Recipe title: {title}\n\nRecipe:\n{content}\n\n"
                    f"Prior Q&A:\n{qa_block or '(none)'}\n"
                    "Either ask one clarifying question or confirm ready with final title and content."
                ),
            }
        ],
    )
    raw_json = _strip_json_fence(response.content[0].text)
    data = json.loads(raw_json)
    if data.get("action") not in ("ask", "ready"):
        raise ValueError("Invalid clarification action")
    return data


def claude_apply_recipe_edit(full_markdown: str, instruction: str) -> str:
    """Return full updated markdown file body (including title line if present)."""
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=(
            "You edit recipe text. Apply the user's instruction precisely. "
            "Return ONLY the complete updated recipe as markdown (keep a leading # title line if present). "
            "No preamble, no markdown fences."
        ),
        messages=[
            {
                "role": "user",
                "content": f"Current recipe:\n\n{full_markdown}\n\nEdit instruction:\n{instruction}",
            }
        ],
    )
    return response.content[0].text.strip()


def claude_summarize_recipe_brief(content: str) -> str:
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system="Summarise this recipe in 2-4 short sentences for a Telegram message. Be warm and clear.",
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text.strip()


def claude_parse_reminder(user_text: str, now_local_hint: str) -> dict[str, str]:
    """
    Extract reminder for scheduling. JSON keys:
    reminder_text, time_24h (HH:MM), day (today|tomorrow|YYYY-MM-DD)
    """
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=(
            "Extract a one-off reminder from the user message. "
            f"The user's local 'now' context for interpretation: {now_local_hint}. "
            "If they say a time without date, use today if that time is still in the future, else tomorrow. "
            "Respond ONLY with JSON: "
            '{"reminder_text":"<short text>","time_24h":"HH:MM","day":"today"|"tomorrow"|"YYYY-MM-DD"} '
            "time_24h must be 24-hour 00:00-23:59. No markdown fences."
        ),
        messages=[{"role": "user", "content": user_text}],
    )
    raw_json = _strip_json_fence(response.content[0].text)
    return json.loads(raw_json)


def claude_match_morning_dish(dish: str, recipe_names: list[str]) -> dict[str, Any]:
    """
    Pick best matching recipe name from list, or none. JSON:
    { "match": true, "recipe_name": "<exactly one from list>" } or { "match": false }
    """
    names_block = "\n".join(f"- {n}" for n in recipe_names) or "(no saved recipes)"
    response = get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=200,
        system=(
            "The user said what they plan to cook. Match to at most one recipe from the list if it clearly "
            "refers to the same dish (synonyms OK). If none fit, match false. "
            'Respond ONLY with JSON: {"match":true,"recipe_name":"<one from list>"} or {"match":false}. '
            "recipe_name must exactly equal an entry from the list when match is true. No markdown fences."
        ),
        messages=[
            {
                "role": "user",
                "content": f"User dish: {dish!r}\n\nSaved recipe names:\n{names_block}",
            }
        ],
    )
    raw_json = _strip_json_fence(response.content[0].text)
    return json.loads(raw_json)
