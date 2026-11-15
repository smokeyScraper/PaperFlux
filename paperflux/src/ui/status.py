"""Sidebar status: live progress while processing, calm facts otherwise."""
import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

import streamlit as st

from paperflux.src.services.schedule_rules import next_scheduled_run
from paperflux.src.ui.timeutil import format_local, relative

logger = logging.getLogger("paperflux.ui.status")

POLL_WHILE_PROCESSING = 6
POLL_WHILE_IDLE = 60


def user_timezone() -> Optional[str]:
    try:
        return st.context.timezone
    except Exception:
        return None


def transition(was_processing: bool, is_processing: bool) -> Optional[str]:
    """What a poll of the status fragment should do: None, 'started' or 'finished'."""
    if was_processing == is_processing:
        return None
    return "finished" if was_processing else "started"


def next_update_text(meta: Any, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    nxt = next_scheduled_run(now, meta.last_processed_date if meta.has_run else None)
    if nxt <= now:
        return "The next update starts within the hour."
    return f"Next update {relative(nxt, now)} ({format_local(nxt, user_timezone())})."


def start_processing(db: Any, processor: Any) -> None:
    """Run processing in a daemon thread. Never touches st.session_state from the thread."""
    db.set_processing_status(True)

    def run() -> None:
        try:
            asyncio.run(processor.process_papers())
        except Exception:
            logger.exception("Manual processing failed")
        finally:
            if not processor.is_running:
                db.set_processing_status(False)

    threading.Thread(target=run, daemon=True, name="manual-processing").start()


def render_status(db: Any, processor: Any, meta: Any, pending_count: int) -> None:
    """A fragment that polls only as often as needed and reruns the page on change."""
    was_processing = bool(meta.is_processing)
    interval = POLL_WHILE_PROCESSING if was_processing else POLL_WHILE_IDLE

    @st.fragment(run_every=interval)
    def panel() -> None:
        current = db.get_processing_metadata()
        change = transition(was_processing, bool(current.is_processing))
        if change:
            if change == "finished":  # refresh the feed and tell the reader
                st.session_state["just_finished"] = True
            st.rerun()  # full rerun: also switches the poll interval
        _draw(db, processor, current, pending_count)

    panel()


def _draw(db: Any, processor: Any, meta: Any, pending_count: int) -> None:
    now = datetime.now(timezone.utc)
    tz = user_timezone()
    if meta.is_processing:
        if meta.progress_total and getattr(meta, "stage", "papers") == "podcasts":
            st.progress(min(1.0, meta.progress_done / meta.progress_total),
                        text=f"Recording podcasts… {meta.progress_done}/{meta.progress_total} done")
        elif meta.progress_total:
            st.progress(min(1.0, meta.progress_done / meta.progress_total),
                        text=f"Analysing papers… {meta.progress_done}/{meta.progress_total} ready")
        else:
            st.progress(0.0, text="Fetching today's papers…")
        st.caption("This updates by itself. Keep reading, new papers appear as they finish.")
        return

    if meta.has_run:
        st.caption(f"✅ Updated {relative(meta.last_processed_date, now)} · "
                   f"{format_local(meta.last_processed_date, tz)}")
    else:
        st.caption("No update has run yet.")
    st.caption(f"⏭️ {next_update_text(meta, now)}")

    if pending_count:
        st.info(f"{pending_count} paper{'s are' if pending_count != 1 else ' is'} waiting for free-tier "
                "model quota. They finish automatically once it resets.")

    if db.should_process_today():
        label = "Resume processing" if pending_count else "Process papers now"
        if st.button(label, key="process_btn", icon=":material/play_arrow:", width="stretch"):
            start_processing(db, processor)
            st.rerun()
