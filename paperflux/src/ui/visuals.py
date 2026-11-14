import json
import re
from typing import Any, Optional

import streamlit as st

from paperflux.src.ui.mermaid import render_mermaid
from paperflux.src.visuals.chart import ChartSpec
from paperflux.src.visuals.flow import FlowSpec, layout_flow, render_svg
from paperflux.src.visuals.gif import render_gif
from paperflux.src.visuals.svg_sanitizer import sanitize_svg  # noqa: F401  (re-exported)

_VIEWBOX = re.compile(r"""viewBox\s*=\s*['"]([\d.\s]+)['"]""", re.IGNORECASE)


# ---------------------------------------------------------------- caching ----
@st.cache_data(show_spinner=False, max_entries=256)
def _flow_svg(spec_json: str) -> Optional[tuple[str, int]]:
    spec = FlowSpec.from_raw(json.loads(spec_json))
    if spec is None:
        return None
    layout = layout_flow(spec)
    return render_svg(layout), int(layout.height) + 16


@st.cache_data(show_spinner=False, max_entries=32)
def _flow_gif(spec_json: str) -> Optional[bytes]:
    spec = FlowSpec.from_raw(json.loads(spec_json))
    return render_gif(layout_flow(spec)) if spec else None


# ---------------------------------------------------------------- entry ------
def render_insight_visual(visual: Optional[dict[str, Any]], key: str = "visual") -> None:
    """Render any stored visual: new flow/chart types and legacy svg/mermaid/vega-lite."""
    visual = visual or {}
    kind = str(visual.get("type") or "").lower()
    if kind == "flow":
        render_flow(visual.get("spec") or {}, key)
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
    else:
        st.caption("No visual was generated for this insight.")


# ---------------------------------------------------------------- flow -------
def render_flow(spec: dict[str, Any], key: str = "flow") -> None:
    spec_json = json.dumps(spec, sort_keys=True)
    rendered = _flow_svg(spec_json)
    if not rendered:
        st.caption("This diagram could not be drawn.")
        return
    svg, height = rendered
    st.iframe(f'<div style="width:100%">{svg}</div>', height=height, width="stretch")
    _flow_extras(spec_json, key)


@st.fragment
def _flow_extras(spec_json: str, key: str) -> None:
    """Step-by-step text + on-demand GIF. A fragment, so clicking does not rerun the page."""
    spec = FlowSpec.from_raw(json.loads(spec_json))
    if spec is None:
        return
    left, right = st.columns([3, 1])
    with left:
        with st.expander("Read the walkthrough"):
            for index, step in enumerate(spec.steps, start=1):
                st.markdown(f"{index}. {step.caption}")
    with right:
        if st.button("🎞️ Make GIF", key=f"gif-{key}-{spec.fingerprint()}"):
            with st.spinner("Rendering GIF…"):
                data = _flow_gif(spec_json)
            if data:
                st.image(data)
                st.download_button(
                    "Download GIF",
                    data,
                    file_name=f"paperflux-{spec.fingerprint()}.gif",
                    mime="image/gif",
                    key=f"dl-{key}-{spec.fingerprint()}",
                )


# ---------------------------------------------------------------- charts -----
def _render_chart(visual: dict[str, Any]) -> None:
    chart = ChartSpec.from_raw(visual.get("spec"))
    if chart is None:
        st.caption("Chart data was missing or invalid.")
        return
    try:
        st.vega_lite_chart(chart.to_vega_lite(), width="stretch")
    except Exception as exc:  # rendering must never break the page
        st.warning(f"Could not render chart: {exc}")
        return
    caption = visual.get("caption") or (f"Source: {chart.source}" if chart.source else "")
    if caption:
        st.caption(caption)


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
        st.vega_lite_chart(spec, width="stretch")
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
