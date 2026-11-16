"""How many people are reading PaperFlux right now.

One Redis sorted set for the whole service, not per paper. Each browser session
heartbeats its id with the current timestamp; ids older than the window are
dropped. The Space does not run Redis. Set REDIS_URL to an external database
(Upstash or Redis Cloud). With no URL, or when Redis is down, the count is
omitted and the page still loads.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

logger = logging.getLogger("paperflux.presence")

KEY = "paperflux:readers"
WINDOW_SECONDS = 120
_RETRY_AFTER = 60.0

_client = None
_down_until = 0.0


def readers_label(count: int) -> str:
    return "1 reading now" if count == 1 else f"{count} reading now"


def beat(session_id: str, client=None, now: Optional[float] = None) -> Optional[int]:
    """Record this session and return how many sessions are inside the window.

    ``None`` means presence is not configured or Redis could not be reached.
    """
    if not session_id:
        return None
    redis = client if client is not None else _redis()
    if redis is None:
        return None
    stamp = time.time() if now is None else now
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
