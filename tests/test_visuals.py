import io
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from paperflux.src.visuals.chart import ChartSpec
from paperflux.src.visuals.flow import FlowSpec, layout_flow, render_svg
from paperflux.src.visuals.gif import _drawable, render_gif
from paperflux.src.visuals.svg_sanitizer import sanitize_svg

LORA = {
    "title": "LoRA: freeze the model, learn a low-rank update",
    "nodes": [
        {"id": "x", "label": "Input x", "detail": "hidden state", "kind": "input"},
        {"id": "w", "label": "Frozen weight W", "detail": "pretrained, d×d", "kind": "model"},
        {"id": "a", "label": "Down-proj A", "detail": "d×r, trainable"},
        {"id": "b", "label": "Up-proj B", "detail": "r×d, init to zero"},
        {"id": "s", "label": "Sum", "kind": "decision"},
        {"id": "y", "label": "Output h", "kind": "output"},
    ],
    "edges": [
        {"from": "x", "to": "w"}, {"from": "x", "to": "a", "label": "rank r"},
        {"from": "a", "to": "b"}, {"from": "w", "to": "s"},
        {"from": "b", "to": "s"}, {"from": "s", "to": "y"},
    ],
    "steps": [
        {"caption": "Input enters the frozen weight", "nodes": ["x", "w"]},
        {"caption": "And the tiny trainable path", "nodes": ["a", "b"]},
        {"caption": "Both are summed", "nodes": ["s", "y"]},
    ],
}
CYCLE = {
    "nodes": [{"id": str(i), "label": f"Stage {i}"} for i in range(5)],
    "edges": [{"from": "0", "to": "1"}, {"from": "1", "to": "2"}, {"from": "2", "to": "3"},
              {"from": "3", "to": "4"}, {"from": "0", "to": "3", "label": "skip"},
              {"from": "4", "to": "1", "label": "feedback"}],
}


def overlaps(a, b):
    return not (a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y)


class FlowTest(unittest.TestCase):
    def check_layout(self, raw):
        spec = FlowSpec.from_raw(raw)
        self.assertIsNotNone(spec)
        layout = layout_flow(spec)
        placed = list(layout.placed.values())
        for i, a in enumerate(placed):
            for b in placed[i + 1:]:
                self.assertFalse(overlaps(a, b), f"{a.node.id} overlaps {b.node.id}")
            self.assertGreaterEqual(a.x + layout.ox, 0)
            self.assertLessEqual(a.x + a.w + layout.ox, layout.width)
            self.assertLessEqual(a.y + a.h + layout.oy, layout.height)
        return spec, layout

    def test_layouts_do_not_overlap_and_svg_is_well_formed(self):
        for raw in (LORA, CYCLE):
            spec, layout = self.check_layout(raw)
            ET.fromstring(render_svg(layout))

    def test_cycle_becomes_back_edge_and_long_edge_gets_routed(self):
        _, layout = self.check_layout(CYCLE)
        self.assertEqual(sum(g.back for g in layout.edges), 1)

    def test_deep_pipeline_switches_to_top_to_bottom(self):
        raw = {"nodes": [{"id": str(i), "label": f"Step {i}"} for i in range(8)],
               "edges": [{"from": str(i), "to": str(i + 1)} for i in range(7)]}
        _, layout = self.check_layout(raw)
        self.assertFalse(layout.horizontal)

    def test_every_node_is_revealed_by_some_step(self):
        spec = FlowSpec.from_raw({**LORA, "steps": [{"caption": "only x", "nodes": ["x"]}]})
        covered = {n for s in spec.steps for n in s.nodes}
        self.assertEqual(covered, {n.id for n in spec.nodes})

    def test_steps_are_derived_when_missing(self):
        spec = FlowSpec.from_raw({k: v for k, v in LORA.items() if k != "steps"})
        self.assertGreaterEqual(len(spec.steps), 3)

    def test_messy_model_output_is_normalised_not_crashing(self):
        raw = {
            "nodes": ["Encoder", {"label": "Decoder", "kind": "weird"}, {"id": "Encoder", "label": "Dup"}, {"label": ""}, 42],
            "edges": [{"source": "Encoder", "target": "Decoder", "label": "x" * 80},
                      {"from": "nope", "to": "Decoder"}, {"from": "Decoder", "to": "Decoder"}],
        }
        spec = FlowSpec.from_raw(raw)
        self.assertEqual(len(spec.nodes), 3)
        self.assertEqual(len(spec.edges), 1)
        self.assertLessEqual(len(spec.edges[0].label), 18)
        self.assertEqual(spec.nodes[1].kind, "process")

    def test_unusable_specs_return_none(self):
        self.assertIsNone(FlowSpec.from_raw({"nodes": [{"label": "alone"}]}))
        self.assertIsNone(FlowSpec.from_raw("nope"))
        self.assertIsNone(FlowSpec.from_raw({"nodes": []}))

    def test_node_cap(self):
        raw = {"nodes": [{"label": f"N{i}"} for i in range(30)]}
        self.assertEqual(len(FlowSpec.from_raw(raw).nodes), 10)

    def test_model_text_cannot_inject_markup_into_svg(self):
        evil = '"><script>alert(1)</script><img src=x onerror=alert(2)>'
        raw = {"title": evil, "caption": evil,
               "nodes": [{"id": "a", "label": evil, "detail": evil}, {"id": "b", "label": "ok"}],
               "edges": [{"from": "a", "to": "b", "label": evil}],
               "steps": [{"caption": evil, "nodes": ["a", "b"]}]}
        svg = render_svg(layout_flow(FlowSpec.from_raw(raw)))
        root = ET.fromstring(svg)  # still well-formed
        tags = {el.tag.rsplit("}", 1)[-1] for el in root.iter()}
        self.assertNotIn("script", tags)
        self.assertNotIn("img", tags)
        # the hostile text must survive only as escaped character data
        self.assertNotIn("<script", svg)
        self.assertNotIn("<img", svg)
        self.assertIn("&lt;script&gt;", svg)

    def test_animation_css_present_and_respects_reduced_motion(self):
        svg = render_svg(layout_flow(FlowSpec.from_raw(LORA)))
        self.assertIn("@keyframes nd0", svg)
        self.assertIn("@keyframes ed2", svg)
        self.assertIn("prefers-reduced-motion:no-preference", svg)
        self.assertIn("infinite", svg)

    def test_keyframes_are_structurally_valid(self):
        import re
        svg = render_svg(layout_flow(FlowSpec.from_raw(LORA)))
        css = re.search(r"<style>(.*?)</style>", svg, re.S).group(1)
        defined = set(re.findall(r"@keyframes (\w+)\{", css))
        used = set(re.findall(r"animation:(\w+) ", css))
        self.assertTrue(used)
        self.assertEqual(used - defined, set(), "animation references a missing @keyframes")
        blocks = {}
        for m in re.finditer(r"@keyframes (\w+)\{", css):
            depth, j = 1, m.end()
            while depth:
                depth += (css[j] == "{") - (css[j] == "}")
                j += 1
            blocks[m.group(1)] = css[m.end():j - 1]
        self.assertEqual(set(blocks), defined)
        self.assertGreaterEqual(len(blocks), 12)  # 4 keyframe sets x 3 steps: not a vacuous pass
        for name, body in blocks.items():
            pcts = [float(x) for x in re.findall(r"([\d.]+)%\{", body)]
            self.assertEqual(pcts, sorted(set(pcts)), f"{name}: percentages must be strictly increasing")
            self.assertEqual((pcts[0], pcts[-1]), (0.0, 100.0), f"{name}: must span 0%..100%")
        # every class the CSS animates is actually used by an element
        for cls in ("nd s0", "ed s1", "ar s2", "cp s2"):
            self.assertIn(f'class="{cls}', svg)

    def test_spec_roundtrips(self):
        spec = FlowSpec.from_raw(LORA)
        again = FlowSpec.from_raw(spec.to_dict())
        self.assertEqual(spec.fingerprint(), again.fingerprint())


class GifTest(unittest.TestCase):
    def test_gif_is_animated_looping_and_reasonably_small(self):
        data = render_gif(layout_flow(FlowSpec.from_raw(LORA)))
        image = Image.open(io.BytesIO(data))
        self.assertEqual(image.format, "GIF")
        self.assertGreaterEqual(image.n_frames, 12)
        self.assertEqual(image.info.get("loop"), 0)
        self.assertLess(len(data), 600_000)

    def test_unsupported_glyphs_are_mapped_not_tofu(self):
        out = _drawable("d×d → r≥1 日本", 28)
        self.assertIn("dxd", out)
        self.assertIn("->", out)
        self.assertNotIn("日", out)


class ChartTest(unittest.TestCase):
    def test_numbers_are_parsed_from_strings_and_bad_points_dropped(self):
        chart = ChartSpec.from_raw({
            "kind": "bar", "title": "Acc", "y_label": "%",
            "series": [{"name": "Ours", "points": [{"label": "MMLU", "value": "85.3%"}, ["GSM8K", 92]]},
                       {"name": "Base", "points": [{"label": "MMLU", "value": 81}, {"label": "GSM8K", "value": "n/a"}]}],
        })
        self.assertEqual(chart.series[0][1], [("MMLU", 85.3), ("GSM8K", 92.0)])
        self.assertEqual(chart.series[1][1], [("MMLU", 81.0)])
        spec = chart.to_vega_lite()
        self.assertEqual(len(spec["data"]["values"]), 3)
        self.assertIn("xOffset", spec["layer"][0]["encoding"])  # grouped bars for 2 series

    def test_too_little_data_is_rejected(self):
        self.assertIsNone(ChartSpec.from_raw({"series": [{"name": "a", "points": [{"label": "x", "value": 1}]}]}))
        self.assertIsNone(ChartSpec.from_raw({"series": []}))
        self.assertIsNone(ChartSpec.from_raw("x"))


class SanitizerTest(unittest.TestCase):
    NS = 'xmlns="http://www.w3.org/2000/svg"'

    def assert_clean(self, svg, forbidden):
        out = sanitize_svg(svg).lower()
        for token in forbidden:
            self.assertNotIn(token, out)

    def test_attack_vectors_are_neutralised(self):
        n = self.NS
        cases = {
            f'<svg {n}><script>alert(1)</script><rect width="5" height="5"/></svg>': ["script", "alert"],
            f'<svg {n} onload="alert(1)"><rect onclick="x()" width="5" height="5"/></svg>': ["onload", "onclick"],
            f'<svg {n}><a href="#"><animate attributeName="href" to="javascript:alert(1)"/></a></svg>': ["javascript"],
            f'<svg {n}><use href="#a"><set attributeName="href" to="data:text/html,x"/></use></svg>': ["data:text", "<set"],
            f'<svg {n}><foreignObject><iframe src="//evil"/></foreignObject></svg>': ["foreignobject", "evil", "iframe"],
            f'<svg {n}><use href="javascript:alert(1)"/></svg>': ["javascript"],
            f'<svg {n}><use href="http://evil/x.svg#a"/></svg>': ["evil"],
            f'<svg {n}><style>@import url(http://evil/x.css);</style></svg>': ["@import", "evil"],
            f'<svg {n}><rect style="fill:url(http://evil/x)" width="1" height="1"/></svg>': ["evil"],
            f'<svg {n}><image href="http://evil/x.png"/></svg>': ["<image", "evil"],
        }
        for svg, forbidden in cases.items():
            self.assert_clean(svg, forbidden)

    def test_doctype_entities_and_garbage_fail_closed(self):
        self.assertEqual(sanitize_svg('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg"/>'), "")
        self.assertEqual(sanitize_svg("<svg><rect></svg>"), "")
        self.assertEqual(sanitize_svg("not svg"), "")
        self.assertEqual(sanitize_svg(""), "")

    def test_legit_svg_survives(self):
        good = (f'<svg {self.NS} viewBox="0 0 100 50"><defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
                '<rect width="100" height="50" fill="url(#g)"/><circle r="5"><animate attributeName="opacity" values="0;1" dur="1s"/></circle>'
                '<text x="5" y="5">Hi &amp; bye</text></svg>')
        out = sanitize_svg(good)
        self.assertIn("linearGradient", out)
        self.assertIn("<animate", out)
        self.assertIn("Hi &amp; bye", out)
        self.assertIn("viewBox", out)


if __name__ == "__main__":
    unittest.main()
