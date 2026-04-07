"""Localization utility — Vietnamese primary, English fallback."""
from __future__ import annotations

_TEXTS: dict[str, dict[str, str]] = {}


def load_texts(data: list[dict]) -> None:
    """Load LocalText data (from Idea.xlsx) into memory."""
    for row in data:
        key = row.get("Key")
        if not key:
            continue
        _TEXTS[key] = {
            "vi": row.get("Tiếng Việt (VI)", ""),
            "en": row.get("English (EN)", ""),
        }


def t(key: str, lang: str = "vi") -> str:
    entry = _TEXTS.get(key)
    if entry is None:
        return key
    return entry.get(lang) or entry.get("vi") or key
