"""Streamlit views for one stored paper (Overview and Insights tabs)."""
from typing import Any

import streamlit as st

from paperflux.src.ui.visuals import render_insight_visual

_VIDEO_EXT = (".mp4", ".webm")


def has_explainer_pack(paper: dict[str, Any]) -> bool:
    doc = paper.get("insights") or {}
    return doc.get("schema_version") == 3 and bool(doc.get("insights"))


def render_overview(paper: dict[str, Any]) -> None:
    doc = paper.get("insights") or {}
    paper_id = paper.get("paper_id", "paper")
    if doc.get("tldr"):
        st.info(f"**TL;DR.** {doc['tldr']}")

    numbers = doc.get("key_numbers") or []
    if numbers:
        cols = st.columns(len(numbers))
        for col, item in zip(cols, numbers):
            with col:
                st.metric(item.get("label", ""), item.get("value", ""))
                if item.get("context"):
                    st.caption(item["context"])

    concept = doc.get("core_concept") or {}
    if concept.get("name"):
        st.markdown(f"### The core idea: {concept['name']}")
        if concept.get("explanation"):
            st.markdown(concept["explanation"])
        if concept.get("analogy"):
            st.markdown(f"> 💡 *{concept['analogy']}*")
        if concept.get("visual"):
            render_insight_visual(concept["visual"], key=f"{paper_id}-core")

    media = paper.get("media_urls") or []
    if media:
        st.markdown("#### From the authors")
        for index, url in enumerate(media):
            if url.lower().split("?")[0].endswith(_VIDEO_EXT):
                st.video(url)
            else:
                st.image(url)

    links = []
    if paper.get("github_repo"):
        links.append(f"[Code]({paper['github_repo']})")
    if paper.get("project_page"):
        links.append(f"[Project page]({paper['project_page']})")
    if links:
        st.markdown(" · ".join(links))


def render_insights(paper: dict[str, Any]) -> None:
    doc = paper.get("insights") or {}
    paper_id = paper.get("paper_id", "paper")
    items = doc.get("insights") or []
    if items:
        for idx, insight in enumerate(items, start=1):
            st.markdown(f"### {idx}. {insight.get('title', 'Insight')}")
            if insight.get("summary"):
                st.markdown(insight["summary"])
            if insight.get("why_it_matters"):
                st.markdown(f"**Why it matters:** {insight['why_it_matters']}")
            visual = insight.get("visual") or insight.get("diagram") or {}
            render_insight_visual(visual, key=f"{paper_id}-i{idx}")
            if insight.get("evidence"):
                st.caption(f"Where in the paper: {insight['evidence']}")
            st.divider()
    elif doc.get("raw"):
        st.warning("Insights could not be parsed; showing raw model output.")
        st.markdown(doc["raw"])
    elif doc.get("parse_error"):
        st.warning(f"Insights are not ready yet: {doc['parse_error']}")
    else:
        st.warning("Insights are not available for this paper.")
