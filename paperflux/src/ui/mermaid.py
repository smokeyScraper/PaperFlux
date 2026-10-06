import html
import re
import uuid

import streamlit as st


def render_svg(code: str, min_height: int = 340) -> None:
    """Render a standalone SVG diagram cleanly inside Streamlit."""
    if not code or not code.strip():
        st.caption("No visual was generated for this insight.")
        return

    svg_content = code.strip()

    # Strip code block fences if present
    fenced = re.match(r"^```(?:[a-zA-Z0-9_\-]+)?\s*\n?(.*?)\n?```$", svg_content, re.DOTALL)
    if fenced:
        svg_content = fenced.group(1).strip()

    # Calculate reasonable height if viewBox is present
    viewbox_match = re.search(
        r'viewBox=["\']\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s*["\']',
        svg_content,
        re.IGNORECASE,
    )
    calculated_height = min_height
    if viewbox_match:
        try:
            vb_w = float(viewbox_match.group(1))
            vb_h = float(viewbox_match.group(2))
            if vb_w > 0:
                estimated = int(720.0 * (vb_h / vb_w)) + 40
                calculated_height = max(min_height, min(estimated, 800))
        except (ValueError, ZeroDivisionError):
            pass

    svg_id = f"svg-{uuid.uuid4().hex[:10]}"
    component = f"""
    <div id="{svg_id}" style="width:100%; display:flex; justify-content:center; align-items:center; overflow-x:auto; padding:8px 0;">
      <style>
        #{svg_id} svg {{
          max-width: 100%;
          height: auto;
          display: block;
          margin: 0 auto;
          border-radius: 8px;
        }}
      </style>
      {svg_content}
    </div>
    """
    st.components.v1.html(component, height=calculated_height, scrolling=True)


def render_mermaid(code: str, height: int = 420, diagram_type: str = "mermaid") -> None:
    """Render a diagram inside Streamlit (auto-detects SVG or Mermaid)."""
    if not code or not code.strip():
        st.caption("No diagram was generated for this insight.")
        return

    clean_code = code.strip()
    if diagram_type == "svg" or "<svg" in clean_code.lower():
        render_svg(clean_code, min_height=height)
        return

    diagram_id = f"mmd-{uuid.uuid4().hex[:10]}"
    escaped = html.escape(clean_code)
    min_height = max(height, 160 + clean_code.count("\n") * 22)
    component = f"""
    <div id="wrap-{diagram_id}">
      <pre id="src-{diagram_id}" style="display:none">{escaped}</pre>
      <div id="{diagram_id}"></div>
    </div>
    <script type="module">
      import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
      mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' }});
      const src = document.getElementById('src-{diagram_id}').textContent;
      const target = document.getElementById('{diagram_id}');
      try {{
        const {{ svg }} = await mermaid.render('svg-{diagram_id}', src);
        target.innerHTML = svg;
      }} catch (err) {{
        target.innerHTML = '<pre style="color:#b00020;white-space:pre-wrap;">' +
          String(err) + '</pre>';
      }}
    </script>
    """
    st.components.v1.html(component, height=min_height, scrolling=True)


def render_diagram(diagram_type: str, code: str, height: int = 420) -> None:
    """Render either an SVG or Mermaid diagram."""
    render_mermaid(code, height=height, diagram_type=diagram_type)
