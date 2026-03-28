"""Recipe file I/O: save, load, list, search (plain .md under RECIPES_DIR)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from config import RECIPES_DIR

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "_", name)
    return name[:60]


def recipe_path_for_name(name: str) -> Path:
    return RECIPES_DIR / f"{slugify(name)}.md"


def save_recipe(name: str, content: str) -> Path:
    from datetime import datetime

    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    filename = recipe_path_for_name(name)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"# {name}\n\n")
        f.write(f"_Saved on {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n\n")
        f.write(content.strip())
    logger.info("Recipe saved: path=%s title=%s", filename, name)
    return filename


def list_recipes() -> list[Path]:
    if not RECIPES_DIR.is_dir():
        return []
    files = sorted(RECIPES_DIR.glob("*.md"))
    # Exclude journal if it were named .md in recipes — journal is journal.md inside recipes dir
    files = [f for f in files if f.name != "journal.md"]
    logger.debug("List recipes scan: dir=%s count=%d", RECIPES_DIR, len(files))
    return files


def stem_display_name(path: Path) -> str:
    return path.stem.replace("_", " ").title()


def load_all_recipes_text() -> str:
    files = list_recipes()
    if not files:
        return ""
    parts = []
    for f in files:
        parts.append(f"### {stem_display_name(f)}\n{f.read_text(encoding='utf-8')}")
    return "\n\n---\n\n".join(parts)


def read_recipe_file(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def search_recipes_by_ingredient(ingredient: str) -> list[tuple[str, str]]:
    results = []
    ingredient_lower = ingredient.lower()
    for f in list_recipes():
        content = f.read_text(encoding="utf-8")
        if ingredient_lower in content.lower():
            results.append((stem_display_name(f), content))
    return results


def list_recipe_names_for_matching() -> list[tuple[str, Path]]:
    """(display_name, path) for each recipe."""
    return [(stem_display_name(f), f) for f in list_recipes()]


def find_recipe_path_by_dish_name(dish: str) -> Path | None:
    """Match user dish string to a recipe file (exact slug, stem contains, or normalized equality)."""
    dish_slug = slugify(dish)
    if not dish_slug:
        return None
    for f in list_recipes():
        if f.stem == dish_slug:
            return f
    dish_norm = dish.lower().strip()
    for f in list_recipes():
        stem_spaced = f.stem.replace("_", " ").lower()
        if dish_norm == stem_spaced or dish_norm in stem_spaced or stem_spaced in dish_norm:
            return f
    return None


def resolve_recipe_by_user_pick(text: str) -> Path | None:
    """Resolve loose user text (substring / stem) to a recipe path."""
    t = text.strip().lower()
    if not t:
        return None
    candidates: list[Path] = []
    for f in list_recipes():
        stem = f.stem.replace("_", " ").lower()
        display = stem_display_name(f).lower()
        if t == f.stem.lower() or t == stem or t in stem or t in display:
            candidates.append(f)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    for f in candidates:
        if f.stem.lower() == slugify(text):
            return f
    return None


def path_for_display_name(name: str) -> Path | None:
    n = name.strip().lower()
    for f in list_recipes():
        if stem_display_name(f).lower() == n:
            return f
    return resolve_recipe_by_user_pick(name)


def write_full_markdown(path: Path, full_markdown: str) -> None:
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(full_markdown.strip() + "\n", encoding="utf-8")
    logger.info("Recipe updated: path=%s", path)
