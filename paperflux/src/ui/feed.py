"""The home feed: today's papers as scannable cards."""
from typing import Any

import streamlit as st

from paperflux.src.ui.content import (
    ANALYSIS_ONLY,
    EXPLAINER,
    PENDING,
    blurb,
    format_date,
    paper_status,
    short_authors,
)
from paperflux.src.ui.nav import goto

_BADGES = {
    EXPLAINER: ":violet-badge[:material/animation: Animated explainer]",
    PENDING: ":orange-badge[:material/hourglass_top: Diagrams coming soon]",
    ANALYSIS_ONLY: ":gray-badge[:material/article: Analysis]",
}


def render_feed(papers: list[dict[str, Any]]) -> None:
    st.markdown("### Today's papers")
    st.caption(f"{len(papers)} papers · ranked by Hugging Face upvotes · open one to see the explainer")
    for rank, paper in enumerate(papers, start=1):
        _card(rank, paper)


def _card(rank: int, paper: dict[str, Any]) -> None:
    pid = paper["paper_id"]
    meta = [short_authors(paper.get("authors") or []), format_date(paper)]
    if paper.get("upvotes"):
        meta.append(f"👍 {paper['upvotes']}")
    with st.container(border=True):
        text, action = st.columns([6, 1], vertical_alignment="center")
        with text:
            st.markdown(f"##### {rank}. {paper.get('title', 'Untitled')}")
            st.caption(" · ".join(m for m in meta if m))
            description = blurb(paper)
            if description:
                st.markdown(description)
            badge = _BADGES.get(paper_status(paper))
            if badge:
                st.markdown(badge)
        with action:
            st.button(
                "Open →",
                key=f"open-{pid}",
                type="primary" if rank == 1 else "secondary",
                on_click=goto,
                args=(pid,),
                width="stretch",
            )


def render_empty(processing: bool, next_update_text: str) -> None:
    with st.container(border=True):
        if processing:
            st.markdown("### ⚙️ Preparing today's papers")
            st.write("The first ones will appear here automatically as soon as they are ready. "
                     "You don't need to refresh.")
        else:
            st.markdown("### 📭 No papers yet")
            st.write(f"PaperFlux updates every weekday morning. {next_update_text}")
