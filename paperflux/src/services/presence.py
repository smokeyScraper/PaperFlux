"""How many people are reading PaperFlux right now.

One counter for the whole service, not per paper. Each browser session
heartbeats its id; ids older than the window drop off.

A single Space is one process, so the count lives in memory. The home screen
shows it; an open paper hides it, and that visit still counts. Set REDIS_URL
when more than one process should share the same number.
If that Redis is down, the badge hides and the page still loads.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

logger = logging.getLogger("paperflux.presence")

KEY = "paperflux:readers"
WINDOW_SECONDS = 120
_RETRY_AFTER = 60.0

_client = None
_down_until = 0.0
_local: dict[str, float] = {}
_local_lock = threading.Lock()


def readers_label(count: int) -> str:
    return "1 reading now" if count == 1 else f"{count} reading now"


def beat(session_id: str, client=None, now: Optional[float] = None) -> Optional[int]:
    """Record this session and return how many sessions are inside the window.

    ``None`` means Redis is configured but could not be reached. With no
    ``REDIS_URL``, the count is kept in this process.
    """
    if not session_id:
        return None
    stamp = time.time() if now is None else now
    if client is not None:
        return _beat_redis(client, session_id, stamp)
    if os.getenv("REDIS_URL", "").strip():
        redis = _redis()
        if redis is None:
            return None
        return _beat_redis(redis, session_id, stamp)
    return _beat_local(session_id, stamp)


def _beat_redis(redis, session_id: str, stamp: float) -> Optional[int]:
    try:
        pipe = redis.pipeline()
        pipe.zadd(KEY, {session_id: stamp})
        pipe.zremrangebyscore(KEY, "-inf", stamp - WINDOW_SECONDS)
        pipe.zcard(KEY)
        _, _, count = pipe.execute()
        return int(count)
    except Exception:
        logger.warning("Presence update failed; the reader count is hidden until Redis answers")
        _trip()
        return None


def _beat_local(session_id: str, stamp: float) -> int:
    cutoff = stamp - WINDOW_SECONDS
    with _local_lock:
        _local[session_id] = stamp
        for member in [m for m, score in _local.items() if score <= cutoff]:
            del _local[member]
        return len(_local)


def _redis():
    global _client
    url = os.getenv("REDIS_URL", "").strip()
    if not url or time.time() < _down_until:
        return None
    if _client is None:
        import redis
        _client = redis.Redis.from_url(
            url, socket_connect_timeout=0.4, socket_timeout=0.4, decode_responses=True,
        )
    return _client


def _trip() -> None:
    global _client, _down_until
    _client = None
    _down_until = time.time() + _RETRY_AFTER
