from __future__ import annotations

import json
import re
from typing import Any


INSIGHTS_SCHEMA_VERSION = 2

_FENCE = re.compile(
    r"```(svg|mermaid|vega-lite|vegalite|json)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def parse_insights_payload(raw_text: str) -> dict[str, Any]:
    """Parse model output (JSON or Markdown fences) into a stable insights document."""
    candidate = _extract_json_object(raw_text)
    insights: list[dict[str, Any]] = []
    if candidate is not None:
        insights = _insights_from_json(candidate)

    if not insights:
        insights = _insights_from_markdown(raw_text)

    if not insights:
        return {
            "schema_version": INSIGHTS_SCHEMA_VERSION,
            "insights": [],
            "raw": raw_text,
            "parse_error": "Model did not return insights JSON or Markdown sections",
        }

    return {
        "schema_version": INSIGHTS_SCHEMA_VERSION,
        "insights": insights[:3],
        "raw": raw_text,
    }


def _insights_from_json(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = candidate.get("insights")
    if not isinstance(raw_items, list):
        return []
    cleaned = []
    for item in raw_items[:3]:
        if not isinstance(item, dict):
            continue
        visual = _normalize_visual(item.get("visual") or item.get("diagram") or {})
        cleaned.append(
            {
                "title": str(item.get("title") or "Insight").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "visual": visual,
                "diagram": visual,
            }
        )
    return cleaned


def _insights_from_markdown(text: str) -> list[dict[str, Any]]:
    chunks = re.split(r"(?m)^##\s+", text.strip())
    cleaned: list[dict[str, Any]] = []
    for chunk in chunks[1:]:
        if not chunk.strip():
            continue
        lines = chunk.splitlines()
        title = re.sub(r"^\d+\.\s*", "", lines[0].strip())
        if title.lower() in {"insights", "paper insights", "visuals"}:
            continue
        body = "\n".join(lines[1:])
        visual = {"type": "svg", "code": "", "spec": None}
        fence = _FENCE.search(body)
        if fence:
            lang = fence.group(1).lower()
            payload = fence.group(2).strip()
            body = (body[: fence.start()] + body[fence.end() :]).strip()
            visual = _visual_from_fence(lang, payload)
        cleaned.append(
            {
                "title": title or "Insight",
                "summary": body.strip(),
                "visual": visual,
                "diagram": visual,
            }
        )
        if len(cleaned) >= 3:
            break
    return cleaned


def _visual_from_fence(lang: str, payload: str) -> dict[str, Any]:
    if lang in {"vega-lite", "vegalite", "json"}:
        spec = None
        try:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                spec = parsed
        except json.JSONDecodeError:
            spec = None
        return {"type": "vega-lite", "code": payload, "spec": spec}
    if lang == "svg":
        return {"type": "svg", "code": payload, "spec": None}
    return {"type": "mermaid", "code": payload, "spec": None}


def _normalize_visual(visual: Any) -> dict[str, Any]:
    if not isinstance(visual, dict):
        return {"type": "mermaid", "code": str(visual), "spec": None}

    raw_type = str(visual.get("type") or "svg").strip().lower()
    if raw_type in {"vegalite", "vega_lite", "vega-lite", "chart"}:
        raw_type = "vega-lite"
    elif raw_type not in {"svg", "mermaid", "vega-lite"}:
        raw_type = "svg"

    spec = visual.get("spec")
    if isinstance(spec, str):
        try:
            parsed = json.loads(spec)
            spec = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            spec = None
    if spec is not None and not isinstance(spec, dict):
        spec = None

    code = str(visual.get("code") or "").strip()
    if raw_type == "vega-lite" and spec is None and code:
        try:
            parsed = json.loads(code)
            if isinstance(parsed, dict):
                spec = parsed
        except json.JSONDecodeError:
            pass

    return {"type": raw_type, "code": code, "spec": spec}


def _extract_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    else:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
