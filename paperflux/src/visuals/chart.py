"""Chart visuals: the model supplies raw numbers, we build the Vega-Lite spec.

Models are unreliable at writing valid Vega-Lite; they are fine at reading a
results table. So the schema asks for plain series/points and this module turns
them into a consistent, validated spec.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional

MAX_SERIES = 6
MAX_POINTS = 14
KINDS = ("bar", "line", "scatter")
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")

# The paper's method in the accent colour, baselines in greys that read on light and dark pages.
HIGHLIGHT_COLOR = "#6366f1"
BASELINE_COLORS = ["#94a3b8", "#64748b", "#cbd5e1", "#475569", "#a8b3c4"]


class Point(NamedTuple):
    label: str
    value: float
    x: Optional[float] = None  # scatter only: the cost / size axis


def _to_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _NUMBER.search(str(value or "").replace(",", ""))
        if not match:
            return None
        try:
            number = float(match.group(0))
        except ValueError:
            return None
    return number if math.isfinite(number) else None


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _point(raw: Any) -> Optional[Point]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        label, value, x = raw[0], raw[1], raw[2] if len(raw) > 2 else None
    elif isinstance(raw, dict):
        label = raw.get("label", raw.get("name"))
        value = raw.get("value", raw.get("y"))
        x = raw.get("x")
        if label is None and isinstance(x, str) and not _NUMBER.fullmatch(x.strip().replace(",", "")):
            label, x = x, None  # older output put the category in "x"
    else:
        return None
    number, label_text = _to_float(value), _clip(label, 28)
    if number is None or not label_text:
        return None
    return Point(label_text, number, _to_float(x) if x is not None else None)


@dataclass
class ChartSpec:
    kind: str
    title: str
    x_label: str
    y_label: str
    series: list[tuple[str, list[Point]]]
    source: str = ""
    highlight: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> Optional["ChartSpec"]:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "bar").strip().lower()
        kind = kind if kind in KINDS else "bar"
        series: list[tuple[str, list[Point]]] = []
        for s_index, item in enumerate((raw.get("series") or [])[:MAX_SERIES]):
            if not isinstance(item, dict):
                continue
            points = [p for p in map(_point, (item.get("points") or [])[:MAX_POINTS]) if p]
            if kind == "scatter":
                points = [p for p in points if p.x is not None]
            if points:
                series.append((_clip(item.get("name") or f"Series {s_index + 1}", 28), points))
        total = sum(len(p) for _, p in series)
        if not series or total < 2:
            return None
        names = [name for name, _ in series]
        highlight = _clip(raw.get("highlight"), 28)
        return cls(
            kind=kind,
            title=_clip(raw.get("title"), 80),
            x_label=_clip(raw.get("x_label"), 40),
            y_label=_clip(raw.get("y_label") or raw.get("unit"), 40),
            series=series,
            source=_clip(raw.get("source"), 90),
            highlight=highlight if highlight in names else "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "source": self.source,
            "highlight": self.highlight,
            "series": [
                {
                    "name": name,
                    "points": [
                        {"label": p.label, "value": p.value, **({"x": p.x} if p.x is not None else {})}
                        for p in points
                    ],
                }
                for name, points in self.series
            ],
        }

    def _color(self) -> Optional[dict[str, Any]]:
        if len(self.series) < 2:
            return None
        color: dict[str, Any] = {"field": "series", "type": "nominal", "title": None,
                                 "sort": [name for name, _ in self.series]}
        if self.highlight:
            greys = iter(BASELINE_COLORS * 2)
            names = [name for name, _ in self.series]
            color["scale"] = {
                "domain": names,
                "range": [HIGHLIGHT_COLOR if n == self.highlight else next(greys) for n in names],
            }
        return color

    def to_vega_lite(self) -> dict[str, Any]:
        rows = [
            {"label": p.label, "series": name, "value": p.value, **({"x": p.x} if p.x is not None else {})}
            for name, points in self.series
            for p in points
        ]
        color = self._color()
        y_enc: dict[str, Any] = {"field": "value", "type": "quantitative", "title": self.y_label or None}

        if self.kind == "scatter":
            layers = self._scatter_layers(rows, y_enc, color)
        else:
            labels = list(dict.fromkeys(r["label"] for r in rows))
            x_enc: dict[str, Any] = {
                "field": "label",
                "type": "nominal",
                "sort": labels,
                "title": self.x_label or None,
                "axis": {"labelAngle": -30 if max(map(len, labels)) > 10 else 0, "labelLimit": 160},
            }
            if self.kind == "line":
                layers = [{
                    "mark": {"type": "line", "point": True, "strokeWidth": 2.5},
                    "encoding": {"x": x_enc, "y": y_enc, **({"color": color} if color else {})},
                }]
            else:
                bar_enc = {"x": x_enc, "y": y_enc}
                text_enc = {"x": x_enc, "y": y_enc,
                            "text": {"field": "value", "type": "quantitative", "format": ".4~g"}}
                if color:
                    bar_enc["xOffset"] = {"field": "series", "sort": color["sort"]}
                    bar_enc["color"] = color
                    text_enc["xOffset"] = {"field": "series", "sort": color["sort"]}
                layers = [
                    {"mark": {"type": "bar", "cornerRadiusEnd": 3}, "encoding": bar_enc},
                    {"mark": {"type": "text", "dy": -7, "fontSize": 11}, "encoding": text_enc},
                ]

        spec: dict[str, Any] = {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "data": {"values": rows},
            "layer": layers,
            "height": 300,
        }
        if self.title:
            spec["title"] = {"text": self.title, "anchor": "start", "fontSize": 15}
        return _strip_none(spec)

    def _scatter_layers(self, rows: list[dict[str, Any]], y_enc: dict[str, Any],
                        color: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
        xs = [r["x"] for r in rows]
        log = min(xs) > 0 and max(xs) / min(xs) >= 100
        x_enc: dict[str, Any] = {
            "field": "x",
            "type": "quantitative",
            "title": self.x_label or None,
            "scale": {"type": "log"} if log else {"zero": False},
        }
        y_scaled = {**y_enc, "scale": {"zero": False}}
        dots = {"x": x_enc, "y": y_scaled, **({"color": color} if color else {})}
        names = {"x": x_enc, "y": y_scaled, "text": {"field": "label"}}
        return [
            {"mark": {"type": "point", "filled": True, "size": 140, "opacity": 1}, "encoding": dots},
            {"mark": {"type": "text", "align": "left", "dx": 9, "dy": -6, "fontSize": 11}, "encoding": names},
        ]


def _strip_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj
