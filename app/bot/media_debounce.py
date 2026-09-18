"""Hermes ALBUM-DEBOUNCE port (re-implemented, minimal).

Hermes: adapter.MEDIA_GROUP_WAIT_SECONDS=0.8 + _queue_media_group_event: album
items sharing media_group_id debounce into ONE event so the 2nd photo never
interrupts the 1st. Atlas: transport receipt photos in hr.handle_hr_receipt —
grouped photos collapse to one download + one card. Singles pass through.
No new deps (asyncio only).
"""

from __future__ import annotations

import asyncio

MEDIA_GROUP_WAIT_SECS = 0.8

_pending: dict[str, list] = {}
_tasks: dict[str, asyncio.Task] = {}


async def debounce_album(update, context, flush) -> bool:
    """Buffer album photos; True=held (flush later), False=single (handle now)."""
    group_id = getattr(update.message, "media_group_id", None)
    if not group_id:
        return False
    key = str(group_id)
    _pending.setdefault(key, []).append(update)
    old = _tasks.get(key)
    if old is not None and not old.done():
        old.cancel()
    _tasks[key] = asyncio.get_running_loop().create_task(_flush(key, context, flush))
    return True


async def _flush(key: str, context, flush) -> None:
    await asyncio.sleep(MEDIA_GROUP_WAIT_SECS)
    updates = _pending.pop(key, [])
    _tasks.pop(key, None)
    if not updates:
        return
    try:
        await flush(updates[-1], context, len(updates))
    except asyncio.CancelledError:
        _pending.setdefault(key, []).extend(updates)
        raise
    except Exception:
        pass
