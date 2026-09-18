"""Vendored Hermes telegram helpers + one atlas glue helper (no logic changes)."""

from app.bot.vendor_hermes.telegram_entities import expand_link_entities
from app.bot.vendor_hermes.telegram_ids import (
    looks_like_telegram_username,
    normalize_telegram_chat_id,
    parse_telegram_username_target,
)


def visible_text(message) -> str:
    """User-visible text with hidden text_link URLs inlined; raw fallback."""
    try:
        return expand_link_entities(message)
    except Exception:
        return getattr(message, "text", None) or getattr(message, "caption", None) or ""


__all__ = [
    "expand_link_entities",
    "looks_like_telegram_username",
    "normalize_telegram_chat_id",
    "parse_telegram_username_target",
    "visible_text",
]
