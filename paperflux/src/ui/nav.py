"""URL-backed navigation. The URL is the single source of truth, so every paper
and tab is deep-linkable, shareable, and survives a refresh."""
from typing import Optional

import streamlit as st

from paperflux.src.ui.content import LABEL_TO_SLUG, VIEW_LABELS


def goto(paper_id: Optional[str] = None, view: Optional[str] = None) -> None:
    """Callback-safe: change the URL; Streamlit reruns afterwards."""
    st.query_params.clear()
    if paper_id:
        st.query_params["paper"] = paper_id
        if view and view != "overview":
            st.query_params["view"] = view


def current_view() -> str:
    view = st.query_params.get("view", "overview")
    return view if view in VIEW_LABELS else "overview"


def set_view(paper_id: str, view: str) -> None:
    """Switch tab programmatically (e.g. from a call-to-action inside a view)."""
    st.session_state[f"view-{paper_id}"] = VIEW_LABELS[view]
    if view == "overview":
        st.query_params.pop("view", None)
    else:
        st.query_params["view"] = view


def view_selector(paper_id: str) -> str:
    """Segmented control kept in sync with ?view=. Only the selected view is rendered."""
    key = f"view-{paper_id}"
    if key not in st.session_state:
        st.session_state[key] = VIEW_LABELS[current_view()]
    label = st.segmented_control(
        "View",
        list(VIEW_LABELS.values()),
        key=key,
        required=True,
        label_visibility="collapsed",
        width="stretch",
    )
    slug = LABEL_TO_SLUG.get(label, "overview")
    if slug != current_view():
        if slug == "overview":
            st.query_params.pop("view", None)
        else:
            st.query_params["view"] = slug
    return slug
