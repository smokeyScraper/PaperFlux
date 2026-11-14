import json
import unittest
from unittest.mock import MagicMock

from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.agents.paper_insights.agent import PaperInsightsAgent, build_repair_text
from paperflux.src.agents.paper_insights.plan import (
    PLAN_SCHEMA, extract_json, is_usable, parse_plan,
)
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import AGENTS_DIR


def good_plan(n_insights=2):
    flow = {"type": "flow", "title": "How", "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}, {"id": "c", "label": "C"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
            "steps": [{"caption": "one", "nodes": ["a", "b"]}, {"caption": "two", "nodes": ["c"]}]}
    chart = {"type": "chart", "title": "Acc", "chart": {"kind": "bar", "source": "Table 1",
             "series": [{"name": "Ours", "points": [{"label": "x", "value": 1.5}, {"label": "y", "value": 2.5}]}]}}
    return {"tldr": "They did X and got Y.", "core_concept": {"name": "Idea", "explanation": "Because.", "analogy": "Like Z.", "animation": flow},
            "key_numbers": [{"label": "Acc", "value": "91%", "context": "vs 88 (Table 1)"}],
            "insights": [{"title": f"I{i}", "takeaway": "t", "why_it_matters": "w", "evidence": "Fig 1", "visual": flow if i % 2 == 0 else chart}
                         for i in range(n_insights)]}


class ParsePlanTest(unittest.TestCase):
    def test_parses_a_good_plan(self):
        doc, problems = parse_plan(json.dumps(good_plan(3)))
        self.assertEqual(doc["schema_version"], 3)
        self.assertEqual(len(doc["insights"]), 3)
        self.assertEqual(doc["insights"][1]["visual"]["type"], "chart")
        self.assertEqual(doc["core_concept"]["visual"]["type"], "flow")
        self.assertTrue(is_usable(doc))
        self.assertEqual(problems, [])

    def test_tolerates_fences_prose_and_trailing_commas(self):
        with_commas = json.dumps(good_plan()).replace("}]", "},]").replace("]}", "],}")
        self.assertNotEqual(with_commas, json.dumps(good_plan()))
        with self.assertRaises(json.JSONDecodeError):
            json.loads(with_commas)  # really invalid for a strict parser
        raw = "Sure! ```json\n" + with_commas + "\n``` hope that helps"
        doc, problems = parse_plan(raw)
        self.assertTrue(is_usable(doc), problems)
        self.assertIsNotNone(extract_json("prefix " + json.dumps(good_plan()) + " suffix"))

    def test_bad_visuals_become_problems_not_crashes(self):
        plan = good_plan()
        plan["insights"][0]["visual"] = {"type": "flow", "title": "x", "nodes": [{"label": "alone"}]}
        plan["insights"][1]["visual"] = {"type": "chart", "title": "x", "chart": {"series": []}}
        doc, problems = parse_plan(json.dumps(plan))
        self.assertEqual(doc["insights"][0]["visual"], {"type": "none"})
        self.assertTrue(any("flow needs" in p for p in problems))
        self.assertFalse(is_usable(doc))

    def test_no_json_returns_none(self):
        doc, problems = parse_plan("I cannot do that")
        self.assertIsNone(doc)
        self.assertTrue(problems)

    def test_caps_lengths_and_counts(self):
        plan = good_plan(3)
        plan["insights"] = plan["insights"] * 3
        plan["key_numbers"] = plan["key_numbers"] * 9
        plan["tldr"] = "w " * 1000
        doc, _ = parse_plan(json.dumps(plan))
        self.assertEqual(len(doc["insights"]), 3)
        self.assertEqual(len(doc["key_numbers"]), 4)
        self.assertLessEqual(len(doc["tldr"]), 400)

    def test_prompt_example_stays_valid_against_the_parser(self):
        _, body = load_skill(AGENTS_DIR / "paper_insights" / "SKILL.md")
        example = body.split("## Example")[1]
        doc, problems = parse_plan(example[example.index("{"):])
        self.assertTrue(is_usable(doc), problems)
        self.assertEqual(problems, [])

    def test_schema_is_json_serialisable_and_requires_core_fields(self):
        json.dumps(PLAN_SCHEMA)
        self.assertEqual(set(PLAN_SCHEMA["required"]), {"tldr", "core_concept", "insights"})


class AgentTest(unittest.TestCase):
    def make(self, client, attach_pdf=True):
        return PaperInsightsAgent(client=client, attach_pdf=attach_pdf)

    def test_happy_path_uses_pdf_and_schema(self):
        client = MagicMock()
        client.can_read_pdf = True
        client.generate_from_pdf.return_value = json.dumps(good_plan())
        doc = self.make(client).run("x.pdf", "writeup", title="T", paper_id="1", abstract="abs")
        self.assertEqual(doc["schema_version"], 3)
        kwargs = client.generate_from_pdf.call_args.kwargs
        self.assertIs(kwargs["response_schema"], PLAN_SCHEMA)
        self.assertIn("writeup", kwargs["user_text"])
        client.generate_text.assert_not_called()

    def test_unusable_first_answer_triggers_one_repair_pass(self):
        client = MagicMock()
        client.can_read_pdf = True
        client.generate_from_pdf.return_value = "{}"
        client.generate_text.return_value = json.dumps(good_plan())
        doc = self.make(client).run("x.pdf", "writeup")
        self.assertTrue(is_usable(doc))
        self.assertEqual(client.generate_text.call_count, 1)
        self.assertIn("could not be used", client.generate_text.call_args.kwargs["user_text"])

    def test_quota_on_pdf_call_falls_back_to_text_only(self):
        client = MagicMock()
        client.can_read_pdf = True
        client.generate_from_pdf.side_effect = QuotaExhaustedError("spent")
        client.generate_text.return_value = json.dumps(good_plan())
        doc = self.make(client).run("x.pdf", "writeup")
        self.assertTrue(is_usable(doc))

    def test_markdown_only_answer_is_salvaged_by_legacy_parser(self):
        client = MagicMock()
        client.can_read_pdf = False
        md = "## Residual routing\nSkips help.\n\n```mermaid\ngraph LR\n A-->B\n```\n\n## Token mixing\nAttention.\n\n```mermaid\ngraph LR\n C-->D\n```\n"
        client.generate_text.return_value = md
        doc = self.make(client).run("x.pdf", "writeup")
        self.assertEqual(doc["schema_version"], 2)
        self.assertEqual(len(doc["insights"]), 2)

    def test_repair_text_lists_problems(self):
        text = build_repair_text("ORIGINAL", "prev", ["insight 1: flow needs at least 2 nodes"])
        self.assertIn("flow needs at least 2 nodes", text)
        self.assertIn("ORIGINAL", text)

    def test_hard_llm_failure_propagates_so_the_processor_can_record_it(self):
        client = MagicMock()
        client.can_read_pdf = False
        client.generate_text.side_effect = LLMError("boom")
        with self.assertRaises(LLMError):
            self.make(client).run("x.pdf", "writeup")


if __name__ == "__main__":
    unittest.main()
