"""Chart visuals: the model supplies raw numbers, we build the Vega-Lite spec.

Models are unreliable at writing valid Vega-Lite; they are fine at reading a
results table. So the schema asks for plain series/points and this module turns
them into a consistent, validated spec.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_SERIES = 6
MAX_POINTS = 14
_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?")


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


@dataclass
class ChartSpec:
    kind: str
    title: str
    x_label: str
    y_label: str
    series: list[tuple[str, list[tuple[str, float]]]]
    source: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> Optional["ChartSpec"]:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "bar").strip().lower()
        kind = kind if kind in {"bar", "line"} else "bar"
        series: list[tuple[str, list[tuple[str, float]]]] = []
        for s_index, item in enumerate((raw.get("series") or [])[:MAX_SERIES]):
            if not isinstance(item, dict):
                continue
            points: list[tuple[str, float]] = []
            for p_index, point in enumerate((item.get("points") or [])[:MAX_POINTS]):
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    label, value = point[0], point[1]
                elif isinstance(point, dict):
                    label = point.get("label", point.get("x", point.get("name")))
                    value = point.get("value", point.get("y"))
                else:
                    continue
                number = _to_float(value)
                label_text = _clip(label, 28)
                if number is None or not label_text:
                    continue
                points.append((label_text, number))
            if points:
                series.append((_clip(item.get("name") or f"Series {s_index + 1}", 28), points))
        total = sum(len(p) for _, p in series)
        if not series or total < 2:
            return None
        return cls(
            kind=kind,
            title=_clip(raw.get("title"), 80),
            x_label=_clip(raw.get("x_label"), 40),
            y_label=_clip(raw.get("y_label") or raw.get("unit"), 40),
            series=series,
            source=_clip(raw.get("source"), 90),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "source": self.source,
            "series": [
                {"name": n, "points": [{"label": l, "value": v} for l, v in pts]}
                for n, pts in self.series
            ],
        }

    def to_vega_lite(self) -> dict[str, Any]:
        rows = [
            {"label": label, "series": name, "value": value}
            for name, pts in self.series
            for label, value in pts
        ]
        labels = list(dict.fromkeys(r["label"] for r in rows))
        multi = len(self.series) > 1
        x_enc: dict[str, Any] = {
            "field": "label",
            "type": "nominal",
            "sort": labels,
            "title": self.x_label or None,
            "axis": {"labelAngle": -30 if max(map(len, labels)) > 10 else 0, "labelLimit": 160},
        }
        y_enc: dict[str, Any] = {
            "field": "value",
            "type": "quantitative",
            "title": self.y_label or None,
        }
        color = {"field": "series", "type": "nominal", "title": None} if multi else {}

        if self.kind == "line":
            base: dict[str, Any] = {
                "mark": {"type": "line", "point": True, "strokeWidth": 2.5},
                "encoding": {"x": x_enc, "y": y_enc, **({"color": color} if multi else {})},
            }
            layers = [base]
        else:
            bar_enc = {"x": x_enc, "y": y_enc}
            text_enc = {"x": x_enc, "y": y_enc, "text": {"field": "value", "type": "quantitative", "format": ".4~g"}}
            if multi:
                bar_enc["xOffset"] = {"field": "series"}
                bar_enc["color"] = color
                text_enc["xOffset"] = {"field": "series"}
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


def _strip_none(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(v) for v in obj]
    return obj
