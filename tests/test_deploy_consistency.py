"""Guards against the failure that broke the Hugging Face build.

HF's generated Dockerfile appends `streamlit==<sdk_version>` (from the README
front matter) to `pip install -r requirements.txt`, so the two must agree.
requirements.txt is generated from poetry.lock; never edit it by hand.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployConsistencyTest(unittest.TestCase):
    def test_readme_sdk_version_matches_pinned_streamlit(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        sdk = re.search(r"^sdk_version:\s*([\d.]+)\s*$", readme, re.M).group(1)
        pinned = re.search(r"^streamlit==([\d.]+)", (ROOT / "requirements.txt").read_text(), re.M).group(1)
        self.assertEqual(sdk, pinned, "README sdk_version must equal the streamlit pin in requirements.txt")

    def test_requirements_match_poetry_lock_for_streamlit(self):
        lock = (ROOT / "poetry.lock").read_text(encoding="utf-8")
        locked = re.search(r'name = "streamlit"\nversion = "([\d.]+)"', lock).group(1)
        pinned = re.search(r"^streamlit==([\d.]+)", (ROOT / "requirements.txt").read_text(), re.M).group(1)
        self.assertEqual(locked, pinned, "regenerate requirements.txt: poetry export --only main --without-hashes")

    def test_requirements_has_runtime_deps_only(self):
        text = (ROOT / "requirements.txt").read_text()
        for dev in ("ruff==", "black==", "flake8==", "google-generativeai=="):
            self.assertNotIn(dev, text)
        self.assertIn("pillow==", text)  # used directly by the GIF exporter


if __name__ == "__main__":
    unittest.main()
