import io
import json
import re
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from paperflux.src.agents.paper_insights.plan import parse_plan
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import AGENTS_DIR
from paperflux.src.visuals.tokens import (
    BLEND_FRAMES,
    TokenSpec,
    draw_slot,
    layout_tokens,
    render_tokens_gif,
    render_tokens_svg,
)

SPEC = {
    "title": "Speculative decoding",
    "tokens": ["The", "quick", {"text": "fox", "state": "hidden"}, {"text": "jumps", "state": "hidden"},
               {"text": "a", "state": "hidden"}],
    "steps": [
        {"caption": "Draft proposes 3", "changes": [{"indices": [2, 3, 4], "state": "new"}]},
        {"caption": "Target verifies", "changes": [{"index": 2, "state": "keep"}, {"index": 3, "state": "keep"},
                                                   {"index": 4, "state": "drop", "tag": "reject"}]},
        {"caption": "Target fixes the last", "changes": [{"index": 4, "state": "new", "text": "the", "tag": "target"}]},
    ],
    "legend": {"new": "proposed", "keep": "verified", "drop": "rejected", "bogus": "x"},
}


def skill_token_example():
    _, body = load_skill(AGENTS_DIR / "paper_insights" / "SKILL.md")
    section = body.split("## Token visuals")[1].split("## Chart visuals")[0]
    return json.loads(re.search(r"```json\n(.*?)\n```", section, re.S).group(1))


class TokenSpecTest(unittest.TestCase):
    def test_states_accumulate_and_text_changes_persist(self):
        slots = TokenSpec.from_raw(SPEC).slots()
        self.assertEqual(len(slots), 4)
        self.assertEqual([t.state for t in slots[0]], ["plain", "plain", "hidden", "hidden", "hidden"])
        self.assertEqual([t.state for t in slots[2]], ["plain", "plain", "keep", "keep", "drop"])
        last = slots[3][4]
        self.assertEqual((last.state, last.text, last.tag), ("new", "the", "target"))
        self.assertEqual(slots[3][2].state, "keep")  # untouched tokens keep their state

    def test_messy_output_is_normalised(self):
        raw = {"tokens": ["a", {"text": ""}, 3, {"text": "b", "state": "weird"}, "c" * 40],
               "steps": [{"caption": "x", "changes": [{"index": 9, "state": "new"}, {"index": "1", "state": "nope"},
                                                       "junk", {"indices": [0, 2], "state": "drop"}]},
                         {"caption": "", "changes": [{"index": 0, "state": "new"}]}]}
        spec = TokenSpec.from_raw(raw)
        self.assertEqual([t.text for t in spec.tokens][:2], ["a", "b"])
        self.assertEqual(spec.tokens[1].state, "plain")
        self.assertLessEqual(len(spec.tokens[2].text), 16)
        self.assertEqual(len(spec.steps), 1)  # the caption-less step is dropped
        self.assertEqual([(c.index, c.state) for c in spec.steps[0].changes], [(1, "plain"), (0, "drop"), (2, "drop")])
        self.assertTrue(any("out of range" in w for w in spec.warnings))

    def test_unusable_specs_return_none(self):
        self.assertIsNone(TokenSpec.from_raw({"tokens": ["only"], "steps": SPEC["steps"]}))
        self.assertIsNone(TokenSpec.from_raw({"tokens": ["a", "b"], "steps": []}))
        self.assertIsNone(TokenSpec.from_raw("nope"))

    def test_unknown_legend_states_are_dropped_and_spec_roundtrips(self):
        spec = TokenSpec.from_raw(SPEC)
        self.assertEqual(list(spec.legend), ["new", "keep", "drop"])
        self.assertEqual(TokenSpec.from_raw(spec.to_dict()).fingerprint(), spec.fingerprint())


class TokenRenderTest(unittest.TestCase):
    def test_svg_is_well_formed_with_valid_keyframes(self):
        svg = render_tokens_svg(layout_tokens(TokenSpec.from_raw(SPEC)))
        ET.fromstring(svg)
        css = re.search(r"<style>(.*?)</style>", svg, re.S).group(1)
        defined = set(re.findall(r"@keyframes (\w+)\{", css))
        used = set(re.findall(r"animation:(\w+) ", css))
        self.assertTrue(used)
        self.assertEqual(used, defined)
        for name in defined:
            body = re.search(r"@keyframes %s\{(.*?)\}\}" % name, css).group(1) + "}"
            pcts = [float(x) for x in re.findall(r"([\d.]+)%\{", body)]
            self.assertEqual(pcts, sorted(set(pcts)), name)
            self.assertEqual((pcts[0], pcts[-1]), (0.0, 100.0), name)

    def test_reduced_motion_shows_exactly_the_end_state(self):
        layout = layout_tokens(TokenSpec.from_raw(SPEC))
        svg = render_tokens_svg(layout)
        final = re.findall(r'<g class="tk[^"]*\bfin"><rect[^>]*/>(?:<text[^>]*>([^<]*)</text>)?', svg)
        self.assertEqual(len(final), len(layout.cells))  # one visible cell per token
        self.assertEqual(final, ["The", "quick", "fox", "jumps", "the"])
        self.assertIn(".tk.fin,.cp.fin{opacity:1}", svg)
        self.assertIn("prefers-reduced-motion:no-preference", svg)

    def test_model_text_cannot_inject_markup(self):
        evil = '"><script>alert(1)</script>'
        raw = {"title": evil, "tokens": [evil, "ok"], "legend": {"new": evil},
               "steps": [{"caption": evil, "changes": [{"index": 0, "state": "new", "tag": evil}]}]}
        svg = render_tokens_svg(layout_tokens(TokenSpec.from_raw(raw)))
        ET.fromstring(svg)
        self.assertNotIn("<script", svg)

    def test_long_sequences_wrap_inside_the_card_without_overlap(self):
        raw = {"tokens": [f"token{i:02d}" for i in range(24)], "steps": [{"caption": "x", "changes": [{"index": 0, "state": "new"}]}]}
        layout = layout_tokens(TokenSpec.from_raw(raw))
        self.assertGreater(len({y for _, y, _, _ in layout.cells}), 1)
        for i, (x, y, w, h) in enumerate(layout.cells):
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + w, layout.width)
            for x2, y2, w2, h2 in layout.cells[i + 1:]:
                self.assertTrue(x + w <= x2 or x2 + w2 <= x or y + h <= y2 or y2 + h2 <= y)

    def test_gif_loops_with_a_blend_between_steps(self):
        layout = layout_tokens(TokenSpec.from_raw(SPEC))
        data = render_tokens_gif(layout)
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.info.get("loop"), 0)
        self.assertEqual(image.n_frames, 1 + len(SPEC["steps"]) * (BLEND_FRAMES + 1))
        self.assertLess(len(data), 400_000)
        self.assertNotEqual(draw_slot(layout, 1).tobytes(), draw_slot(layout, 2).tobytes())


class PromptExampleTest(unittest.TestCase):
    def test_skill_token_example_is_valid_and_parses_into_a_plan(self):
        raw = skill_token_example()
        spec = TokenSpec.from_raw(raw)
        self.assertIsNotNone(spec)
        self.assertEqual(spec.warnings, [])
        self.assertEqual(spec.slots()[-1][5].text, "the")  # the rejected guess is replaced, not appended
        plan = {"tldr": "x", "core_concept": {"name": "n", "explanation": "e", "animation": raw},
                "insights": [{"title": "a", "takeaway": "t", "visual": raw}, {"title": "b", "takeaway": "t", "visual": raw}]}
        doc, problems = parse_plan(json.dumps(plan))
        self.assertEqual(doc["core_concept"]["visual"]["type"], "tokens")
        self.assertEqual(doc["visual_count"], 3)
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
