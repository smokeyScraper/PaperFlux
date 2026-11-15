"""Light/dark toggle.

Streamlit has no Python API to set the theme: the browser stores the visitor's choice in
localStorage and applies it on page load. The toggle writes that same entry and reloads.
Navigation lives in the URL (see nav.py), so the open paper and tab survive the reload.
"""
import streamlit as st

LIGHT, DARK = "light", "dark"

# Must match Streamlit's frontend key `stActiveTheme-<pathname>-v<N>`, whose stored value is
# "Light", "Dark" or "System". Checked against the installed bundle in tests/test_theme.py.
STORAGE_VERSION = 2
_PENDING = "_paperflux_switch_theme"


def current_theme() -> str:
    try:
        return DARK if st.context.theme.type == DARK else LIGHT
    except Exception:
        return LIGHT


def switch_script() -> str:
    """Flip the theme the browser is actually showing (stored choice, else the OS preference)."""
    return f"""<script>
(() => {{
  const key = `stActiveTheme-${{window.location.pathname}}-v{STORAGE_VERSION}`;
  let stored = null;
  try {{ stored = JSON.parse(window.localStorage.getItem(key)); }} catch (e) {{}}
  const dark = stored === "Dark" ||
    (stored !== "Light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  window.localStorage.setItem(key, JSON.stringify(dark ? "Light" : "Dark"));
  window.location.reload();
}})();
</script>"""


def theme_toggle() -> None:
    """The button only. The reload script is emitted by ``apply_theme_switch``, outside the header
    row: injected here it becomes an extra flex item and shoves the button left until the reload."""
    dark = current_theme() == DARK
    if st.button(
        "Light mode" if dark else "Dark mode",
        key="theme-toggle",
        icon=":material/light_mode:" if dark else ":material/dark_mode:",
        help="Switch between light and dark theme",
    ):
        st.session_state[_PENDING] = True


def apply_theme_switch() -> None:
    """Emit the reload script below the header, in a container the script itself hides."""
    if not st.session_state.pop(_PENDING, False):
        return
    st.html(
        "<style>[data-testid='stElementContainer']:has(#paperflux-theme-switch){display:none !important}</style>"
        '<div id="paperflux-theme-switch" hidden></div>'
        + switch_script(),
        unsafe_allow_javascript=True,
    )
