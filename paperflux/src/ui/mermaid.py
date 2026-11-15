import html
import uuid

import streamlit as st

from paperflux.src.ui.theme import DARK, current_theme


def render_mermaid(code: str, height: int = 420) -> None:
    """Render a Mermaid diagram inside Streamlit via mermaid.js."""
    if not code or not code.strip():
        return

    diagram_id = f"mmd-{uuid.uuid4().hex[:10]}"
    escaped = html.escape(code)
    min_height = max(height, 160 + code.count("\n") * 22)
    mermaid_theme = "dark" if current_theme() == DARK else "neutral"
    component = f"""
    <div id="wrap-{diagram_id}">
      <pre id="src-{diagram_id}" style="display:none">{escaped}</pre>
      <div id="{diagram_id}"></div>
    </div>
    <script type="module">
      import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
      mermaid.initialize({{ startOnLoad: false, theme: '{mermaid_theme}', securityLevel: 'strict' }});
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
    st.iframe(component.strip(), height=min_height, width="stretch")
