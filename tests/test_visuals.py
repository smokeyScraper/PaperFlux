import io
import unittest
import xml.etree.ElementTree as ET

from PIL import Image

from paperflux.src.visuals.chart import HIGHLIGHT_COLOR, ChartSpec
from paperflux.src.visuals.chart_gif import ChartFrames, callout, chart_to_gif, direction, nice_ticks
from paperflux.src.visuals.flow import ACCENT, FlowSpec, layout_flow, render_svg
from paperflux.src.visuals.gif import _drawable, _partial, draw_frame, render_gif
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
RICH = {
    "title": "One token through a LoRA layer",
    "nodes": [
        {"id": "x", "label": "Hidden state x", "kind": "input", "example": "x: 4096 dims"},
        {"id": "w", "label": "Frozen W", "detail": "pretrained", "kind": "model", "example": "W x: 4096 dims"},
        {"id": "a", "label": "Down-projection A", "detail": "trainable", "novel": True, "example": "A x: 8 dims"},
        {"id": "b", "label": "Up-projection B", "detail": "trainable, starts at 0", "novel": "yes", "example": "B A x: 4096 dims and then some more text"},
        {"id": "y", "label": "Output h", "kind": "output"},
    ],
    "edges": [{"from": "x", "to": "w"}, {"from": "x", "to": "a"}, {"from": "a", "to": "b"},
              {"from": "w", "to": "y"}, {"from": "b", "to": "y"}],
    "steps": [{"caption": "In", "nodes": ["x", "w"]}, {"caption": "Down", "nodes": ["a"]},
              {"caption": "Up", "nodes": ["b"]}, {"caption": "Out", "nodes": ["y"]}],
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
        for raw in (LORA, RICH):
            spec = FlowSpec.from_raw(raw)
            again = FlowSpec.from_raw(spec.to_dict())
            self.assertEqual(spec.fingerprint(), again.fingerprint())

    def test_novel_nodes_get_a_tag_and_examples_get_a_chip_inside_the_node(self):
        spec, layout = self.check_layout(RICH)
        self.assertEqual([n.id for n in spec.nodes if n.novel], ["a", "b"])  # "yes" counts
        svg = render_svg(layout)
        ET.fromstring(svg)
        self.assertEqual(svg.count(">NEW</text>"), 2)
        self.assertIn(">A x: 8 dims</text>", svg)
        for p in layout.placed.values():
            box = p.example_box()
            if p.node.example:
                bx, by, bw, bh = box
                self.assertTrue(p.x < bx and bx + bw < p.x + p.w and p.y < by and by + bh < p.y + p.h)
                last_text = max(ty for _, ty, _ in p.text_rows())
                self.assertGreater(by, last_text, f"{p.node.id}: chip overlaps the node text")
            else:
                self.assertIsNone(box)
        tag = layout.placed["a"].novel_tag()
        self.assertGreaterEqual(tag[1] + layout.oy, 56, "NEW tag must stay below the header")

    def test_every_edge_carries_a_packet_timed_to_its_step(self):
        import re
        layout = layout_flow(FlowSpec.from_raw(RICH))
        svg = render_svg(layout)
        packets = re.findall(r'<circle class="pk s(\d+)".*?keyTimes="([^"]+)"', svg)
        self.assertEqual(len(packets), len(layout.edges))
        for step, times in packets:
            values = [float(t) for t in times.split(";")]
            self.assertEqual((values[0], values[-1]), (0.0, 1.0))
            self.assertEqual(values, sorted(values))
        self.assertIn(".pk{opacity:0}", svg)  # hidden unless motion is allowed

    def test_example_text_cannot_inject_markup(self):
        evil = '"><script>alert(1)</script>'
        raw = {"nodes": [{"id": "a", "label": "A", "example": evil, "novel": "<b>"}, {"id": "b", "label": "B"}],
               "edges": [{"from": "a", "to": "b"}]}
        svg = render_svg(layout_flow(FlowSpec.from_raw(raw)))
        ET.fromstring(svg)
        self.assertNotIn("<script", svg)
        self.assertEqual(FlowSpec.from_raw(raw).nodes[0].novel, False)

    def test_compare_groups_become_aligned_lanes(self):
        raw = {"nodes": [{"id": "b1", "label": "Tokenize", "group": "Baseline"}, {"id": "b2", "label": "Attend", "group": "Baseline"},
                         {"id": "p1", "label": "Tokenize", "group": "Ours"}, {"id": "p2", "label": "Sparse attend", "group": "Ours", "novel": True},
                         {"id": "p3", "label": "Merge", "group": "Ours"}],
               "edges": [{"from": "b1", "to": "b2"}, {"from": "p1", "to": "p2"}, {"from": "p2", "to": "p3"}]}
        _, layout = self.check_layout(raw)
        P = layout.placed
        self.assertTrue(layout.horizontal)
        self.assertEqual({g[0] for g in layout.groups}, {"Baseline", "Ours"})  # both boxes fit
        self.assertEqual(P["b1"].x, P["p1"].x)  # matching stages line up
        self.assertEqual(P["b2"].x, P["p2"].x)
        self.assertLess(max(P[n].y + P[n].h for n in ("b1", "b2")), min(P[n].y for n in ("p1", "p2", "p3")))
        self.assertEqual(P["b2"].y, P["b1"].y)  # a lane stays on one row even when shorter

    def test_groups_with_edges_between_them_are_not_forced_into_lanes(self):
        raw = {**LORA, "nodes": [{**n, "group": "Left" if i % 2 else "Right"} for i, n in enumerate(LORA["nodes"])]}
        spec, layout = self.check_layout(raw)
        self.assertIsNotNone(spec)


class GifTest(unittest.TestCase):
    def test_gif_is_animated_looping_and_reasonably_small(self):
        for raw in (LORA, RICH):
            data = render_gif(layout_flow(FlowSpec.from_raw(raw)))
            image = Image.open(io.BytesIO(data))
            self.assertEqual(image.format, "GIF")
            self.assertGreaterEqual(image.n_frames, 12)
            self.assertEqual(image.info.get("loop"), 0)
            self.assertLess(len(data), 600_000)

    def test_gif_draws_a_packet_mid_edge_and_the_chip_only_on_arrival(self):
        layout = layout_flow(FlowSpec.from_raw(RICH))
        accent = tuple(int(ACCENT[i:i + 2], 16) for i in (1, 3, 5))
        step = layout.node_step["b"]
        moving, settled = draw_frame(layout, step, 0.5), draw_frame(layout, step, 1.0)
        scale = moving.width / layout.width

        def at(x, y):
            return int((x + layout.ox) * scale), int((y + layout.oy) * scale)

        edge = next(g for g in layout.edges if (g.edge.src, g.edge.dst) == ("a", "b"))
        tip = _partial(edge.polyline(14), 0.5)[-1]
        self.assertLess(sum(abs(a - b) for a, b in zip(moving.getpixel(at(*tip)), accent)), 40)
        self.assertGreater(sum(abs(a - b) for a, b in zip(settled.getpixel(at(*tip)), accent)), 40)
        bx, by, bw, bh = layout.placed["b"].example_box()
        crop = (*at(bx + 2, by + 2), *at(bx + bw - 2, by + bh - 2))
        self.assertNotEqual(moving.crop(crop).tobytes(), settled.crop(crop).tobytes())

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
        self.assertEqual([p[:2] for p in chart.series[0][1]], [("MMLU", 85.3), ("GSM8K", 92.0)])
        self.assertEqual([p[:2] for p in chart.series[1][1]], [("MMLU", 81.0)])
        spec = chart.to_vega_lite()
        self.assertEqual(len(spec["data"]["values"]), 3)
        self.assertIn("xOffset", spec["layer"][0]["encoding"])  # grouped bars for 2 series

    def test_highlight_colours_the_papers_method_and_greys_the_rest(self):
        raw = {"kind": "bar", "highlight": "Ours", "series": [
            {"name": "Base", "points": [{"label": "A", "value": 1}]},
            {"name": "Ours", "points": [{"label": "A", "value": 2}]}]}
        scale = ChartSpec.from_raw(raw).to_vega_lite()["layer"][0]["encoding"]["color"]["scale"]
        self.assertEqual(scale["domain"], ["Base", "Ours"])
        self.assertEqual(scale["range"][1], HIGHLIGHT_COLOR)
        self.assertNotEqual(scale["range"][0], HIGHLIGHT_COLOR)
        unknown = ChartSpec.from_raw({**raw, "highlight": "Nobody"})
        self.assertEqual(unknown.highlight, "")
        self.assertNotIn("scale", unknown.to_vega_lite()["layer"][0]["encoding"]["color"])

    def test_scatter_needs_x_and_uses_a_log_axis_for_wide_ranges(self):
        raw = {"kind": "scatter", "x_label": "Params (M)", "series": [
            {"name": "Ours", "points": [{"label": "LoRA", "x": 0.3, "value": 87.5}]},
            {"name": "Baselines", "points": [{"label": "FT", "x": 125, "value": 87.6},
                                             {"label": "no x", "value": 80}]}]}
        chart = ChartSpec.from_raw(raw)
        self.assertEqual(sum(len(p) for _, p in chart.series), 2)  # the point without x is dropped
        spec = chart.to_vega_lite()
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["scale"], {"type": "log"})
        self.assertEqual(spec["layer"][1]["encoding"]["text"], {"field": "label"})
        self.assertEqual(ChartSpec.from_raw(chart.to_dict()).to_dict(), chart.to_dict())
        self.assertIsNone(ChartSpec.from_raw({"kind": "scatter", "series": [
            {"name": "a", "points": [{"label": "x", "value": 1}, {"label": "y", "value": 2}]}]}))

    def test_chart_gif_callout_respects_which_direction_is_better(self):
        raw = {"kind": "bar", "highlight": "Ours", "y_label": "Perplexity (lower is better)", "series": [
            {"name": "Ours", "points": [{"label": "Wiki", "value": 5.0}, {"label": "C4", "value": 7.5}]},
            {"name": "A", "points": [{"label": "Wiki", "value": 5.5}, {"label": "C4", "value": 7.0}]},
            {"name": "B", "points": [{"label": "Wiki", "value": 6.0}]}]}
        self.assertEqual(callout(ChartSpec.from_raw(raw)), "Ours vs best baseline: Wiki -0.5, C4 +0.5")
        self.assertEqual(direction("Accuracy (%, higher is better)"), "higher")
        self.assertEqual(callout(ChartSpec.from_raw({**raw, "y_label": "Perplexity"})), "")  # unknown: no claim
        self.assertEqual(callout(ChartSpec.from_raw({**raw, "highlight": ""})), "")

    def test_chart_gifs_animate_every_kind_and_bars_start_at_zero(self):
        bar = {"kind": "bar", "highlight": "Ours", "y_label": "Acc (higher is better)", "series": [
            {"name": "Ours", "points": [{"label": "A", "value": 87.5}, {"label": "B", "value": 95}]},
            {"name": "Base", "points": [{"label": "A", "value": 86}, {"label": "B", "value": 94}]}]}
        line = {**bar, "kind": "line"}
        scatter = {"kind": "scatter", "highlight": "Ours", "series": [
            {"name": "Ours", "points": [{"label": "X", "x": 0.3, "value": 87}]},
            {"name": "Base", "points": [{"label": "Y", "x": 125, "value": 88}, {"label": "Z", "x": 1, "value": 85}]}]}
        for raw in (bar, line, scatter):
            image = Image.open(io.BytesIO(chart_to_gif(raw)))
            self.assertGreater(image.n_frames, 15, raw["kind"])
            self.assertEqual(image.info.get("loop"), 0)
        self.assertEqual(ChartFrames(ChartSpec.from_raw(bar)).y_ticks[0], 0)  # no truncated axis
        self.assertIsNone(chart_to_gif({"series": []}))

    def test_nice_ticks_cover_the_data(self):
        for lo, hi in ((0, 95.1), (84.7, 87.6), (-3, 12), (5, 5)):
            ticks = nice_ticks(lo, hi)
            self.assertLessEqual(ticks[0], lo)
            self.assertGreaterEqual(ticks[-1], hi)
            self.assertLessEqual(len(ticks), 12)

    def test_legacy_category_in_x_still_parses(self):
        chart = ChartSpec.from_raw({"series": [{"name": "a", "points": [{"x": "MMLU", "y": 1}, {"x": "GSM8K", "y": 2}]}]})
        self.assertEqual([p.label for p in chart.series[0][1]], ["MMLU", "GSM8K"])

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
