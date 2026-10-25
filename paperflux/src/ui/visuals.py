import json
import re
from typing import Any, Optional

import streamlit as st

from paperflux.src.ui.mermaid import render_mermaid

_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.IGNORECASE)
_ON_ATTR = re.compile(r"""\s+on\w+\s*=\s*(['"]).*?\1""", re.IGNORECASE | re.DOTALL)
_JS_HREF = re.compile(r"""(href|xlink:href)\s*=\s*(['"])javascript:.*?\2""", re.IGNORECASE)
_VIEWBOX = re.compile(r"""viewBox\s*=\s*['"]([\d.\s]+)['"]""", re.IGNORECASE)


def render_insight_visual(visual: Optional[dict[str, Any]]) -> None:
    visual = visual or {}
    visual_type = str(visual.get("type") or "").lower()
    if visual_type in {"vega-lite", "vegalite", "vega_lite", "chart"}:
        _render_vega_lite(visual)
        return
    if visual_type == "mermaid":
        render_mermaid(visual.get("code") or "")
        return
    svg = visual.get("code") or ""
    if "<svg" in svg.lower():
        _render_svg(svg)
        return
    if visual.get("code"):
        render_mermaid(visual.get("code") or "")
        return
    st.caption("No visual was generated for this insight.")


def _render_vega_lite(visual: dict[str, Any]) -> None:
    spec = visual.get("spec")
    if spec is None and visual.get("code"):
        try:
            parsed = json.loads(visual["code"])
            spec = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            spec = None
    if not spec:
        st.caption("Chart spec was missing or invalid.")
        return
    try:
        st.vega_lite_chart(spec, use_container_width=True)
    except Exception as exc:
        st.warning(f"Could not render Vega-Lite chart: {exc}")
        st.code(json.dumps(spec, indent=2)[:4000], language="json")


def _render_svg(raw_svg: str) -> None:
    svg = sanitize_svg(raw_svg)
    if "<svg" not in svg.lower():
        st.caption("SVG visual was empty after sanitizing.")
        return
    height = _svg_height(svg)
    st.components.v1.html(
        f'<div style="width:100%;overflow:auto">{svg}</div>',
        height=height,
        scrolling=True,
    )


def sanitize_svg(svg: str) -> str:
    svg = svg.strip()
    if svg.startswith("```"):
        svg = re.sub(r"^```(?:svg)?\s*", "", svg)
        svg = re.sub(r"\s*```$", "", svg)
    svg = _SCRIPT.sub("", svg)
    svg = _ON_ATTR.sub("", svg)
    svg = _JS_HREF.sub("", svg)
    return svg


def _svg_height(svg: str) -> int:
    match = _VIEWBOX.search(svg)
    if match:
        parts = match.group(1).split()
        if len(parts) >= 4:
            try:
                return int(min(720, max(280, float(parts[3]) * 0.9 + 40)))
            except ValueError:
                pass
    return 480
