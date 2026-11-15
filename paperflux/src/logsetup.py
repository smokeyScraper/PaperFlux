"""Service logging.

``configure_logging`` is safe to call more than once. Streamlit installs its own
root handler before the app runs, and ``basicConfig`` then does nothing, so the
paper id prefix is attached to whatever handlers already exist.

``PAPERFLUX_LOG_LEVEL`` (default INFO) controls the ``paperflux`` logger. DEBUG
adds skipped-quota and other retry detail.

``bind_paper`` makes every log line emitted on that thread while a paper is in
progress start with ``[paper_id]``, including Gemini calls that do not mention
the paper themselves. Lines that already contain the id are left alone.
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager

_paper: contextvars.ContextVar[str] = contextvars.ContextVar("paperflux_paper", default="")

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class PaperPrefix(logging.Filter):
    """Prefix log lines with the paper id bound on this thread, when there is one."""

    def filter(self, record: logging.LogRecord) -> bool:
        pid = _paper.get()
        if not pid or getattr(record, "_paperflux_prefixed", False):
            return True
        rendered = record.getMessage()
        record._paperflux_prefixed = True
        if pid not in rendered:
            record.msg = f"[{pid}] {rendered}"
            record.args = ()
        return True


def configure_logging() -> None:
    raw = os.getenv("PAPERFLUX_LOG_LEVEL", "INFO").strip().upper()
    level = getattr(logging, raw, logging.INFO)
    logging.getLogger("paperflux").setLevel(level)

    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt="%H:%M:%S"))
        root.addHandler(handler)
        root.setLevel(level)

    prefix = PaperPrefix()
    for handler in root.handlers:
        if not any(isinstance(existing, PaperPrefix) for existing in handler.filters):
            handler.addFilter(prefix)


@contextmanager
def bind_paper(paper_id: str):
    """Bind ``paper_id`` for logs emitted on this thread until the block exits."""
    token = _paper.set(paper_id or "")
    try:
        yield
    finally:
        _paper.reset(token)
