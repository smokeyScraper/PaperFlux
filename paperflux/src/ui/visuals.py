import json
import re
from typing import Any, Optional

import streamlit as st

from paperflux.src.ui.mermaid import render_mermaid
from paperflux.src.ui.theme import DARK, LIGHT, current_theme
from paperflux.src.visuals.chart import ChartSpec
from paperflux.src.visuals.flow import FlowSpec, layout_flow, render_svg
from paperflux.src.visuals.tokens import TokenSpec, layout_tokens, render_tokens_svg
from paperflux.src.visuals.svg_sanitizer import sanitize_svg  # noqa: F401  (re-exported)

_VIEWBOX = re.compile(r"""viewBox\s*=\s*['"]([\d.\s]+)['"]""", re.IGNORECASE)
# textColor of each palette in .streamlit/config.toml
_CHART_TEXT = {LIGHT: "#0f172a", DARK: "#e2e8f0"}


# ---------------------------------------------------------------- caching ----
def _parse(kind: str, raw: Any) -> Any:
    return {"flow": FlowSpec, "tokens": TokenSpec, "chart": ChartSpec}[kind].from_raw(raw)


@st.cache_data(show_spinner=False, max_entries=256)
def _animated_svg(kind: str, spec_json: str) -> Optional[tuple[str, int]]:
    spec = _parse(kind, json.loads(spec_json))
    if spec is None:
        return None
    if kind == "flow":
        layout = layout_flow(spec)
        return render_svg(layout), int(layout.height) + 16
    layout = layout_tokens(spec)
    return render_tokens_svg(layout), int(layout.height) + 16


# ---------------------------------------------------------------- entry ------
def render_insight_visual(visual: Optional[dict[str, Any]], key: str = "visual") -> None:
    """Render any stored visual: new flow/chart types and legacy svg/mermaid/vega-lite."""
    visual = visual or {}
    kind = str(visual.get("type") or "").lower()
    if kind in {"flow", "tokens"}:
        _render_animated(kind, visual.get("spec") or {}, key)
    elif kind == "chart":
        _render_chart(visual)
    elif kind in {"vega-lite", "vegalite", "vega_lite"}:
        _render_vega_lite(visual)
    elif kind == "mermaid":
        render_mermaid(visual.get("code") or "")
    elif "<svg" in str(visual.get("code") or "").lower():
        _render_legacy_svg(visual.get("code") or "")
    elif visual.get("code"):
        render_mermaid(visual.get("code") or "")


# ---------------------------------------------------------------- animated ---
def _render_animated(kind: str, spec: dict[str, Any], key: str) -> None:
    """Flows and token sequences: an animated SVG inline, plus the step-by-step text."""
    spec_json = json.dumps(spec, sort_keys=True)
    rendered = _animated_svg(kind, spec_json)
    if not rendered:
        return  # a missing diagram is omitted; the insight text stands on its own
    svg, height = rendered
    st.iframe(f'<div style="width:100%">{svg}</div>', height=height, width="stretch")
    parsed = _parse(kind, json.loads(spec_json))
    if parsed and parsed.steps:
        with st.expander("Read the walkthrough", key=f"walk-{key}"):
            for index, step in enumerate(parsed.steps, start=1):
                st.markdown(f"{index}. {step.caption}")


# ---------------------------------------------------------------- charts -----
def _render_chart(visual: dict[str, Any]) -> None:
    chart = ChartSpec.from_raw(visual.get("spec"))
    if chart is None:
        return
    try:
        st.vega_lite_chart(_themed(chart.to_vega_lite()), width="stretch")
    except Exception as exc:  # rendering must never break the page
        st.warning(f"Could not render chart: {exc}")
        return
    caption = visual.get("caption") or (f"Source: {chart.source}" if chart.source else "")
    if caption:
        st.caption(caption)


def _themed(spec: dict[str, Any]) -> dict[str, Any]:
    """Streamlit themes axes and legends but not text marks (value labels), which stay black."""
    config = dict(spec.get("config") or {})
    config["text"] = {"color": _CHART_TEXT[current_theme()], **(config.get("text") or {})}
    return {**spec, "config": config}


# ---------------------------------------------------------------- legacy -----
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
        st.vega_lite_chart(_themed(spec), width="stretch")
    except Exception as exc:
        st.warning(f"Could not render Vega-Lite chart: {exc}")
        st.code(json.dumps(spec, indent=2)[:4000], language="json")


def _render_legacy_svg(raw_svg: str) -> None:
    svg = sanitize_svg(raw_svg)
    if "<svg" not in svg.lower():
        st.caption("This legacy diagram was removed because it could not be verified as safe.")
        return
    st.iframe(
        f'<div style="width:100%;overflow:auto">{svg}</div>',
        height=_svg_height(svg),
        width="stretch",
    )


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
