"""The "explainer pack" the insights agent asks the model for, and how we parse it.

The model returns *meaning only* (nodes, edges, steps, numbers). Layout, colours
and animation are produced by ``paperflux.src.visuals``.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from paperflux.src.visuals.chart import ChartSpec
from paperflux.src.visuals.flow import NODE_KINDS, FlowSpec

PLAN_SCHEMA_VERSION = 3

_STR = {"type": "STRING"}
_VISUAL_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "type": {"type": "STRING", "enum": ["flow", "chart"]},
        "title": _STR,
        "caption": _STR,
        "nodes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": _STR,
                    "label": _STR,
                    "detail": _STR,
                    "kind": {"type": "STRING", "enum": list(NODE_KINDS)},
                    "group": _STR,
                },
                "required": ["id", "label"],
            },
        },
        "edges": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"from": _STR, "to": _STR, "label": _STR},
                "required": ["from", "to"],
            },
        },
        "steps": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"caption": _STR, "nodes": {"type": "ARRAY", "items": _STR}},
                "required": ["caption", "nodes"],
            },
        },
        "chart": {
            "type": "OBJECT",
            "properties": {
                "kind": {"type": "STRING", "enum": ["bar", "line"]},
                "x_label": _STR,
                "y_label": _STR,
                "source": _STR,
                "series": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": _STR,
                            "points": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {"label": _STR, "value": {"type": "NUMBER"}},
                                    "required": ["label", "value"],
                                },
                            },
                        },
                        "required": ["name", "points"],
                    },
                },
            },
        },
    },
    "required": ["type", "title"],
}

PLAN_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "tldr": _STR,
        "core_concept": {
            "type": "OBJECT",
            "properties": {
                "name": _STR,
                "explanation": _STR,
                "analogy": _STR,
                "animation": _VISUAL_SCHEMA,
            },
            "required": ["name", "explanation"],
        },
        "key_numbers": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"label": _STR, "value": _STR, "context": _STR},
                "required": ["label", "value"],
            },
        },
        "insights": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "title": _STR,
                    "takeaway": _STR,
                    "why_it_matters": _STR,
                    "evidence": _STR,
                    "visual": _VISUAL_SCHEMA,
                },
                "required": ["title", "takeaway", "visual"],
            },
        },
    },
    "required": ["tldr", "core_concept", "insights"],
}


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def extract_json(text: str) -> Optional[dict[str, Any]]:
    """Pull a JSON object out of model output (fences, prose, trailing commas)."""
    stripped = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    else:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        stripped = stripped[start : end + 1]
    for candidate in (stripped, re.sub(r",\s*([}\]])", r"\1", stripped)):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _visual(raw: Any, where: str, problems: list[str]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    wants_chart = str(raw.get("type") or "").lower() == "chart" or bool(raw.get("chart"))
    chart = ChartSpec.from_raw(raw.get("chart") or raw) if wants_chart else None
    if chart is not None:
        return {
            "type": "chart",
            "spec": chart.to_dict(),
            "title": chart.title or _clip(raw.get("title"), 80),
            "caption": _clip(raw.get("caption"), 140) or chart.source,
        }
    flow = FlowSpec.from_raw(raw)
    if flow is not None:
        problems.extend(f"{where}: {w}" for w in flow.warnings)
        return {"type": "flow", "spec": flow.to_dict(), "caption": flow.caption}
    if wants_chart:  # a chart request that carried nodes instead of series, or vice versa
        problems.append(f"{where}: chart had fewer than 2 numeric points")
    else:
        problems.append(f"{where}: flow needs at least 2 nodes")
    return None


def parse_plan(raw_text: str) -> tuple[Optional[dict[str, Any]], list[str]]:
    """Return (document, problems). Document is None if no JSON plan was found."""
    data = extract_json(raw_text)
    if data is None:
        return None, ["Output did not contain a JSON object"]
    problems: list[str] = []

    concept_raw = data.get("core_concept") if isinstance(data.get("core_concept"), dict) else {}
    concept = {
        "name": _clip(concept_raw.get("name"), 60),
        "explanation": _clip(concept_raw.get("explanation"), 700),
        "analogy": _clip(concept_raw.get("analogy"), 300),
        "visual": _visual(
            concept_raw.get("animation") or concept_raw.get("visual"), "core_concept", problems
        ),
    }

    numbers: list[dict[str, str]] = []
    for item in (data.get("key_numbers") or [])[:4]:
        if not isinstance(item, dict):
            continue
        label, value = _clip(item.get("label"), 48), _clip(item.get("value"), 24)
        if label and value:
            numbers.append({"label": label, "value": value, "context": _clip(item.get("context"), 120)})

    insights: list[dict[str, Any]] = []
    for index, item in enumerate((data.get("insights") or [])[:3], start=1):
        if not isinstance(item, dict):
            continue
        visual = _visual(item.get("visual") or item.get("diagram"), f"insight {index}", problems)
        title = _clip(item.get("title"), 90)
        takeaway = _clip(item.get("takeaway") or item.get("summary"), 700)
        if not title and not takeaway:
            continue
        insights.append(
            {
                "title": title or f"Insight {index}",
                "summary": takeaway,
                "why_it_matters": _clip(item.get("why_it_matters"), 400),
                "evidence": _clip(item.get("evidence"), 120),
                "visual": visual or {"type": "none"},
            }
        )

    visuals = sum(1 for i in insights if i["visual"].get("type") in {"flow", "chart"})
    if concept["visual"]:
        visuals += 1
    doc = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "tldr": _clip(data.get("tldr"), 400),
        "core_concept": concept,
        "key_numbers": numbers,
        "insights": insights,
        "warnings": problems,
        "visual_count": visuals,
    }
    if len(insights) < 2:
        problems.append("Fewer than 2 insights were produced")
    if not doc["tldr"]:
        problems.append("Missing tldr")
    return doc, problems


def plan_score(doc: Optional[dict[str, Any]]) -> int:
    """Rough quality score used to pick between an attempt and its repair."""
    if not doc:
        return -1
    score = doc.get("visual_count", 0) * 3 + len(doc.get("insights", [])) + len(doc.get("key_numbers", []))
    if doc.get("tldr"):
        score += 1
    return score


def is_usable(doc: Optional[dict[str, Any]]) -> bool:
    return bool(doc) and doc.get("visual_count", 0) >= 2 and len(doc.get("insights", [])) >= 2
