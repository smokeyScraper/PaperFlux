"""A single paper: header, actions, and one lazily-rendered view at a time."""
import logging
from typing import Any, Callable, Optional

import streamlit as st

from paperflux.src.ui.content import (
    EXPLAINER,
    PENDING,
    VIEW_LABELS,
    find_section,
    format_date,
    neighbors,
    paper_status,
    reading_minutes,
    share_url,
    short_authors,
    split_sections,
    weave_figures,
)
from paperflux.src.ui.nav import current_view, goto, set_view, view_selector
from paperflux.src.ui.paper_view import render_insights, render_overview
from paperflux.src.ui.podcast import render_podcast

logger = logging.getLogger("paperflux.ui.detail")


def render_detail(
    paper: dict[str, Any],
    papers: list[dict[str, Any]],
    base_url: str = "",
    load_audio: Optional[Callable[[str], Optional[bytes]]] = None,
    load_figure: Optional[Callable[[str], Optional[bytes]]] = None,
) -> None:
    pid = paper["paper_id"]
    prev_id, next_id, position, total = neighbors(papers, pid)

    with st.container(horizontal=True, vertical_alignment="center"):
        st.button("← All papers", key="nav-back", on_click=goto)
        st.button("‹ Prev", key="nav-prev", on_click=goto, args=(prev_id,), disabled=prev_id is None,
                  shortcut="K", help="Previous paper (K)")
        st.button("Next ›", key="nav-next", on_click=goto, args=(next_id,), disabled=next_id is None,
                  shortcut="J", help="Next paper (J)")
        st.caption(f"Paper {position} of {total}", width="content")

    st.markdown(f"## {paper.get('title', 'Untitled')}")
    meta = [short_authors(paper.get("authors") or []), format_date(paper), f"arXiv {pid}"]
    if paper.get("upvotes"):
        meta.append(f"👍 {paper['upvotes']}")
    st.caption(" · ".join(m for m in meta if m))

    _action_row(paper, base_url)
    render_podcast(paper, load_audio)
    view = view_selector(pid)

    try:
        _render_view(paper, view, load_figure)
    except Exception:  # one broken view must never take the page down
        logger.exception("Rendering view %s failed for paper %s", view, pid)
        st.error("Something went wrong while showing this tab. Try another tab, or reload the page.")


def _action_row(paper: dict[str, Any], base_url: str) -> None:
    pid = paper["paper_id"]
    with st.container(horizontal=True):
        if paper.get("pdf_url"):
            st.link_button("PDF", paper["pdf_url"], icon=":material/picture_as_pdf:")
        if paper.get("github_repo"):
            st.link_button("Code", paper["github_repo"], icon=":material/code:")
        if paper.get("project_page"):
            st.link_button("Project", paper["project_page"], icon=":material/public:")
        with st.popover("Share", icon=":material/link:"):
            st.caption("Link to this paper (papers are refreshed daily)")
            st.code(share_url(base_url, pid, current_view()), language=None)
        if paper.get("explanation"):
            st.download_button(
                "Analysis",
                data=paper["explanation"],
                file_name=f"paperflux-{pid}.md",
                mime="text/markdown",
                icon=":material/download:",
                on_click="ignore",
                key=f"dl-md-{pid}",
            )


def _render_view(paper: dict[str, Any], view: str, load_figure=None) -> None:
    if view == "overview":
        if paper_status(paper) == EXPLAINER:
            render_overview(paper)
        else:
            _overview_fallback(paper)
    elif view == "insights":
        render_insights(paper)
    elif view == "analysis":
        _render_analysis(paper, load_figure)
    else:
        _render_abstract(paper)


def _render_abstract(paper: dict[str, Any]) -> None:
    _, column, _ = st.columns([1, 6, 1])
    with column:
        st.caption("The authors' own abstract")
        st.markdown(paper.get("summary") or "No abstract available.")


def _overview_fallback(paper: dict[str, Any]) -> None:
    """Papers whose explainer is not ready still deserve a useful first screen."""
    pid = paper["paper_id"]
    explanation = paper.get("explanation") or ""
    if paper_status(paper) == PENDING:
        st.info("✨ The animated explainer for this paper is still being prepared and will appear "
                "here on its own. The written analysis is ready now.", icon=None)
    tldr = find_section(explanation, "tl;dr")
    idea = find_section(explanation, "big idea")
    if tldr:
        st.markdown("#### TL;DR")
        st.markdown(tldr)
    if idea:
        st.markdown("#### The big idea")
        st.markdown(idea)
    if not (tldr or idea):
        st.markdown("#### Abstract")
        st.markdown(paper.get("summary") or "No abstract available.")
    if explanation:
        st.button("Read the full analysis →", key=f"cta-analysis-{pid}", on_click=set_view,
                  args=(pid, "analysis"))


@st.cache_data(show_spinner=False, max_entries=32, ttl=3600)
def _figure_bytes(file_id: str, _load: Callable[[str], Optional[bytes]]) -> Optional[bytes]:
    return _load(file_id) if file_id else None


@st.fragment
def _render_analysis(paper: dict[str, Any], load_figure=None) -> None:
    """Long reads are chunked into sections so people can scan, not scroll."""
    explanation = paper.get("explanation")
    if not explanation:
        st.info("The written analysis is not available for this paper yet.")
        return
    pid = paper["paper_id"]
    _, column, _ = st.columns([1, 6, 1])
    with column:
        st.caption(f"About {reading_minutes(explanation)} min read")
        sections = split_sections(explanation)
        if len(sections) < 2:
            sections = [("", explanation)]
        woven = weave_figures(sections, paper.get("figures") or [])
        if len(sections) < 2 or (len(woven) == 1 and not woven[0][0]):
            _render_blocks(woven[0][1], load_figure)
            return
        if st.toggle("Show all sections expanded", key=f"all-{pid}"):
            for title, blocks in woven:
                st.markdown(f"#### {title}")
                _render_blocks(blocks, load_figure)
        else:
            for index, (title, blocks) in enumerate(woven):
                with st.expander(title, expanded=index < 2):
                    _render_blocks(blocks, load_figure)


def _render_blocks(blocks: list, load_figure) -> None:
    for block in blocks:
        if isinstance(block, str):
            st.markdown(block)
        else:
            _render_figure(block, load_figure)


def _render_figure(fig: dict[str, Any], load_figure) -> None:
    """One of the authors' figures, under the paragraph that discusses it."""
    if load_figure is None:
        return
    data = _figure_bytes(str(fig.get("file_id") or ""), load_figure)
    if not data:
        return
    st.image(data, width="stretch")
    label = fig.get("label") or "Figure"
    caption = (fig.get("caption") or "").strip()
    st.markdown(f"**{label}.** {caption}" if caption else f"**{label}.**")
    page = fig.get("page")
    st.caption(f"From the paper, p. {page}." if page else "From the paper.")


__all__ = ["render_detail", "VIEW_LABELS"]
