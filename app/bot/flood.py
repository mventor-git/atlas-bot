"""Hermes FLOOD-CAP port (re-implemented, ~20 lines).

Hermes: plugins/platforms/telegram/adapter.py — _FLOOD_INLINE_WAIT_CAP_SECS=5.0,
fail-closed ``flood_control:{wait}`` past the cap, single inline retry below it.
Atlas: wrap the send path (keyboards.send_step/finish_step, notify sends) so a
429/RetryAfter waits + retries ONCE, else fails closed with one friendly card.
No spam retries, no new deps.
"""

from __future__ import annotations

import asyncio
import re

FLOOD_INLINE_CAP_SECS = 5.0
FLOOD_FRIENDLY = "Slow mode — Telegram asked us to wait a moment. Try again shortly."


def flood_wait_s(exc: Exception) -> float | None:
    """RetryAfter seconds from a telegram error, else None (never raises)."""
    wait = getattr(exc, "retry_after", None)
    if wait is not None:
        try:
            return max(0.0, float(wait))
        except (TypeError, ValueError):
            return 1.0
    m = re.search(r"retry after (\d+(?:\.\d+)?)", str(exc), re.IGNORECASE)
    return float(m.group(1)) if m else None


async def guarded(send_call):
    """Await ``send_call()``; on flood wait<=cap retry once, else fail-closed None."""
    try:
        return await send_call()
    except Exception as e:
        wait = flood_wait_s(e)
        if wait is None:
            raise
        if wait <= FLOOD_INLINE_CAP_SECS:  # ponytail: one retry only, then closed
            await asyncio.sleep(wait)
            try:
                return await send_call()
            except Exception:
                return None
        return None
