"""Deterministic rule screening shared by the demo pipeline and the LLM fallback."""

from __future__ import annotations

PROMO_TERMS = ("sponsored", "promotion", "advertisement")
TOPIC_TERM = "python"
RELEASE_MARKERS = ("released", "available")


def rule_screen(text: str) -> bool:
    """Return True when the deterministic demo rule accepts the message."""
    normalized = text.casefold()
    if any(term in normalized for term in PROMO_TERMS):
        return False
    return TOPIC_TERM in normalized and any(marker in normalized for marker in RELEASE_MARKERS)
