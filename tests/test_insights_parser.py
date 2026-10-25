import os
import unittest
from unittest.mock import patch

from paperflux.src.agents.paper_insights.parser import parse_insights_payload
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import AGENTS_DIR, collect_api_keys


class InsightsParserTest(unittest.TestCase):
    def test_parses_fenced_json(self):
        raw = """```json
{
  "insights": [
    {
      "title": "Residual routing",
      "summary": "Skip connections keep gradients alive.",
      "diagram": {
        "type": "mermaid",
        "code": "flowchart TD\\n  A[x] --> B[+]"
      }
    },
    {
      "title": "Token mixing",
      "summary": "Attention mixes sequence positions.",
      "diagram": {"type": "mermaid", "code": "graph LR\\n  Q --> K"}
    }
  ]
}
```"""
        parsed = parse_insights_payload(raw)
        self.assertEqual(len(parsed["insights"]), 2)
        self.assertEqual(parsed["insights"][0]["title"], "Residual routing")
        self.assertIn("flowchart TD", parsed["insights"][0]["diagram"]["code"])

    def test_invalid_json_keeps_raw(self):
        parsed = parse_insights_payload("not json at all")
        self.assertEqual(parsed["insights"], [])
        self.assertIn("parse_error", parsed)

    def test_caps_at_three_insights(self):
        parsed = parse_insights_payload(json_insights(4))
        self.assertEqual(len(parsed["insights"]), 3)

    def test_parses_markdown_svg_sections(self):
        raw = """## Residual routing
Skip connections keep gradients alive.

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 40"><rect width="100" height="40"/></svg>
```

## Token mixing
Attention mixes sequence positions.

```vega-lite
{"mark": "bar", "data": {"values": [{"x": "A", "y": 1}]}}
```
"""
        parsed = parse_insights_payload(raw)
        self.assertEqual(len(parsed["insights"]), 2)
        self.assertEqual(parsed["insights"][0]["visual"]["type"], "svg")
        self.assertIn("<svg", parsed["insights"][0]["visual"]["code"])
        self.assertEqual(parsed["insights"][1]["visual"]["type"], "vega-lite")
        self.assertEqual(parsed["insights"][1]["visual"]["spec"]["mark"], "bar")


def json_insights(n: int) -> str:
    items = ",".join(
        f'{{"title": "t{i}", "summary": "s{i}", "diagram": {{"code": "graph TD\\n A{i} --> B{i}"}}}}'
        for i in range(n)
    )
    return '{"insights": [' + items + "]}"


class SkillLoaderTest(unittest.TestCase):
    def test_loads_agent_skills(self):
        for name in ("paper_analyst", "paper_insights"):
            meta, body = load_skill(AGENTS_DIR / name / "SKILL.md")
            self.assertTrue(meta.get("name"))
            self.assertGreater(len(body), 50)


class KeyCollectionTest(unittest.TestCase):
    def test_collects_numbered_keys_and_ignores_model(self):
        env = {
            "GEMINI_ANALYST_API_KEY": "aaa",
            "GEMINI_ANALYST_API_KEY2": "bbb",
            "GEMINI_ANALYST_API_KEY_3": "ccc",
            "GEMINI_ANALYST_MODEL": "gemini-3.5-flash",
        }
        with patch.dict(os.environ, env, clear=False):
            # Remove unrelated keys that would match from the developer machine
            to_delete = [
                k
                for k in os.environ
                if k.startswith("GEMINI_ANALYST_API_KEY") and k not in env
            ]
            for key in to_delete:
                os.environ.pop(key, None)
            keys = collect_api_keys("GEMINI_ANALYST_API_KEY")
        self.assertEqual(keys, ["aaa", "bbb", "ccc"])


if __name__ == "__main__":
    unittest.main()
