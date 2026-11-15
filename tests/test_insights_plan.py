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
    flow = {"type": "flow", "title": "How",
            "nodes": [{"id": "a", "label": "A", "example": "3 tokens"}, {"id": "b", "label": "B", "novel": True, "example": "3 x 8"},
                      {"id": "c", "label": "C"}],
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

    def test_new_visual_fields_survive_parsing(self):
        doc, _ = parse_plan(json.dumps(good_plan()))
        nodes = doc["core_concept"]["visual"]["spec"]["nodes"]
        self.assertEqual([n["novel"] for n in nodes], [False, True, False])
        self.assertEqual(nodes[1]["example"], "3 x 8")
        plan = good_plan()
        plan["insights"][1]["visual"]["chart"].update(kind="scatter", highlight="Ours", series=[
            {"name": "Ours", "points": [{"label": "A", "x": 1, "value": 2}]},
            {"name": "Baselines", "points": [{"label": "B", "x": 10, "value": 1}]}])
        chart = parse_plan(json.dumps(plan))[0]["insights"][1]["visual"]["spec"]
        self.assertEqual((chart["kind"], chart["highlight"]), ("scatter", "Ours"))
        self.assertEqual(chart["series"][1]["points"][0]["x"], 10.0)

    def test_thin_core_animation_is_flagged_without_blocking(self):
        plan = good_plan()
        for node in plan["core_concept"]["animation"]["nodes"]:
            node.pop("novel", None)
            node.pop("example", None)
        doc, problems = parse_plan(json.dumps(plan))
        self.assertTrue(is_usable(doc))  # a warning, not a reason to spend a repair request
        self.assertTrue(any("novel" in p for p in problems))
        self.assertTrue(any("example value" in p for p in problems))

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

    def test_visual_brief_is_sent_before_the_writeup(self):
        client = MagicMock()
        client.can_read_pdf = False
        client.generate_text.return_value = json.dumps(good_plan())
        self.make(client).run("x.pdf", "W" * 30000, visual_brief="### Components\n- Encoder | new: yes")
        text = client.generate_text.call_args.kwargs["user_text"]
        self.assertIn("- Encoder | new: yes", text)
        self.assertLess(text.index("Visual brief"), text.index("Analyst writeup"))
        self.assertIn("[...truncated...]", text)  # the long writeup is cut, the brief is not

    def test_no_brief_means_no_empty_brief_block(self):
        client = MagicMock()
        client.can_read_pdf = False
        client.generate_text.return_value = json.dumps(good_plan())
        self.make(client).run("x.pdf", "writeup")
        self.assertNotIn("Visual brief", client.generate_text.call_args.kwargs["user_text"])

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
