"""The theme toggle relies on Streamlit's frontend storage format, which is not a public API.

If a Streamlit upgrade fails these tests, check how the new frontend stores the visitor's
theme choice and update paperflux/src/ui/theme.py to match.
"""
import re
import unittest
from pathlib import Path

import streamlit

from paperflux.src.ui.theme import STORAGE_VERSION, switch_script


def _frontend_js() -> str:
    static = Path(streamlit.__file__).parent / "static"
    return "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in static.rglob("*.js"))


class ThemeStorageContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _frontend_js()

    def test_storage_key_and_version_match_the_installed_streamlit(self):
        match = re.search(r"(\w+)=(\d+),(\w+)=`stActiveTheme-\$\{window\.location\.pathname\}`", self.js)
        self.assertIsNotNone(match, "Streamlit no longer builds the theme key from the page path")
        version_var, version, base_var = match.groups()
        self.assertEqual(int(version), STORAGE_VERSION)
        self.assertRegex(self.js, rf"ACTIVE_THEME:`\$\{{{base_var}\}}-v\$\{{{version_var}\}}`")

    def test_stored_values_are_light_dark_or_system(self):
        self.assertRegex(self.js, r"===`System`\|\|\w+===`Light`\|\|\w+===`Dark`")


class SwitchScriptTest(unittest.TestCase):
    def test_script_uses_the_same_key_and_only_writes_known_values(self):
        script = switch_script()
        self.assertIn(f"stActiveTheme-${{window.location.pathname}}-v{STORAGE_VERSION}", script)
        self.assertIn('JSON.stringify(dark ? "Light" : "Dark")', script)
        self.assertIn("prefers-color-scheme: dark", script)  # "System" falls back to the OS
        self.assertIn("window.location.reload()", script)


if __name__ == "__main__":
    unittest.main()
