import logging
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("paperflux.app")

# Must be the first Streamlit call.
st.set_page_config(
    page_title="PaperFlux - AI Research Paper Insights",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="auto",
)

from paperflux.src.config.settings import TEMP_DIR  # noqa: E402
from paperflux.src.services.database import DatabaseService  # noqa: E402
from paperflux.src.services.scheduler import PaperScheduler  # noqa: E402
from paperflux.src.ui.content import PENDING, paper_status, sort_papers  # noqa: E402
from paperflux.src.ui.detail import render_detail  # noqa: E402
from paperflux.src.ui.feed import render_empty, render_feed  # noqa: E402
from paperflux.src.ui.nav import goto  # noqa: E402
from paperflux.src.ui.status import next_update_text, render_status  # noqa: E402
from paperflux.src.ui.theme import theme_toggle  # noqa: E402

os.makedirs(TEMP_DIR, exist_ok=True)

ABOUT = """
PaperFlux picks the most-upvoted papers from Hugging Face Daily Papers every weekday and
turns each one into something you can understand in a couple of minutes.

**What you get**
- A TL;DR, the core idea, and the headline numbers
- Step-by-step animated diagrams (export any of them as a GIF) and charts built from the paper's own results
- A layered written analysis: how it works, the math in plain words, the evidence, and where to be skeptical

**How it works**
1. Papers are fetched from Hugging Face and the PDFs from arXiv.
2. An analyst model reads each PDF; a second model plans the diagrams and charts.
3. Our own renderer draws them, so figures stay clean and consistent.

Built with Streamlit, MongoDB and Google Gemini's free tier.
"""


@st.cache_resource(show_spinner=False)
def get_services():
    """One DB handle and one scheduler (which owns the one processor) per server process."""
    db = DatabaseService()
    scheduler = PaperScheduler()
    scheduler.start_scheduler()  # idempotent
    return db, scheduler


@st.dialog("About PaperFlux")
def about_dialog() -> None:
    st.markdown(ABOUT)


def _base_url() -> str:
    try:
        return st.context.url or ""
    except Exception:
        return ""


db, scheduler = get_services()
processor = scheduler.paper_processor

papers = sort_papers(db.get_all_papers())
meta = db.get_processing_metadata()
pending_count = sum(1 for p in papers if paper_status(p) == PENDING)

if st.session_state.pop("just_finished", False):
    st.toast("Fresh papers are ready", icon="✨")

with st.sidebar:
    st.markdown("### 📚 PaperFlux")
    st.caption("AI research papers, explained.")
    render_status(db, processor, meta, pending_count)
    st.divider()
    if st.button("About PaperFlux", icon=":material/info:", width="stretch"):
        about_dialog()

with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
    with st.container(width="content", gap=None):
        st.title("📚 PaperFlux")
        st.caption("Today's most-upvoted AI papers, explained with animated diagrams.")
    theme_toggle()

selected_id = st.query_params.get("paper")
paper = next((p for p in papers if p["paper_id"] == selected_id), None) if selected_id else None

if selected_id and paper is None:
    st.warning("That paper is no longer available. PaperFlux keeps the latest papers only.")
    st.button("Show today's papers", on_click=goto)

if paper is not None:
    render_detail(paper, papers, _base_url())
elif not papers:
    render_empty(meta.is_processing, next_update_text(meta))
else:
    render_feed(papers)

st.divider()
st.caption("PaperFlux · papers from Hugging Face Daily Papers · built with Streamlit and Gemini")
