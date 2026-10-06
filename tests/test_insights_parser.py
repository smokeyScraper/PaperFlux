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
        raw = '{"insights": [{"title": "a", "summary": "s", "diagram": {"code": "x"}}] * 4}'
        # invalid python-in-json; build properly
        raw = json_insights(4)
        parsed = parse_insights_payload(raw)
        self.assertEqual(len(parsed["insights"]), 3)

    def test_parses_svg_diagram(self):
        raw = """```json
{
  "insights": [
    {
      "title": "Sparse Attention Routing",
      "summary": "Routing tokens reduces quadratic complexity to linear.",
      "diagram": {
        "type": "svg",
        "code": "<svg viewBox=\\"0 0 400 100\\" xmlns=\\"http://www.w3.org/2000/svg\\"><rect width=\\"400\\" height=\\"100\\" fill=\\"#0f172a\\"/></svg>"
      }
    }
  ]
}
```"""
        parsed = parse_insights_payload(raw)
        self.assertEqual(len(parsed["insights"]), 1)
        self.assertEqual(parsed["insights"][0]["diagram"]["type"], "svg")
        self.assertIn("<svg", parsed["insights"][0]["diagram"]["code"])

    def test_autodetects_svg_type_when_unspecified(self):
        raw = """{
  "insights": [
    {
      "title": "Cross-Attention Gate",
      "summary": "Gating mechanism.",
      "diagram": {
        "code": "<svg viewBox=\\"0 0 200 100\\"><circle cx=\\"50\\" cy=\\"50\\" r=\\"40\\"/></svg>"
      }
    }
  ]
}"""
        parsed = parse_insights_payload(raw)
        self.assertEqual(parsed["insights"][0]["diagram"]["type"], "svg")

    def test_strips_code_fences_in_diagram_code(self):
        raw = """{
  "insights": [
    {
      "title": "Pipeline",
      "summary": "Stages.",
      "diagram": {
        "type": "svg",
        "code": "```xml\\n<svg><rect/></svg>\\n```"
      }
    }
  ]
}"""
        parsed = parse_insights_payload(raw)
        self.assertEqual(parsed["insights"][0]["diagram"]["code"], "<svg><rect/></svg>")



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
