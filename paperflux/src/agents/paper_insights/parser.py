from __future__ import annotations

import json
import re
from typing import Any


INSIGHTS_SCHEMA_VERSION = 1


def parse_insights_payload(raw_text: str) -> dict[str, Any]:
    """Parse model output into a stable insights document."""
    candidate = _extract_json_object(raw_text)
    if candidate is None:
        return {
            "schema_version": INSIGHTS_SCHEMA_VERSION,
            "insights": [],
            "raw": raw_text,
            "parse_error": "Model did not return valid JSON",
        }

    insights = candidate.get("insights")
    if not isinstance(insights, list):
        insights = []

    cleaned = []
    for item in insights[:3]:
        if not isinstance(item, dict):
            continue
        diagram = item.get("diagram") or item.get("visual") or {}
        if not isinstance(diagram, dict):
            raw_str = str(diagram).strip()
            raw_type = "svg" if "<svg" in raw_str.lower() else "mermaid"
            diagram = {"type": raw_type, "code": raw_str}

        raw_code = str(diagram.get("code") or diagram.get("content") or "").strip()
        raw_code = _strip_code_fences(raw_code)
        raw_type = str(diagram.get("type") or "").strip().lower()

        if raw_type == "svg" or "<svg" in raw_code.lower():
            diag_type = "svg"
        elif raw_type in {"mermaid", "mmd"}:
            diag_type = "mermaid"
        else:
            diag_type = "svg" if "<svg" in raw_code.lower() else (raw_type or "mermaid")

        cleaned.append(
            {
                "title": str(item.get("title") or "Insight").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "diagram": {
                    "type": diag_type,
                    "code": raw_code,
                },
            }
        )

    return {
        "schema_version": INSIGHTS_SCHEMA_VERSION,
        "insights": cleaned,
        "raw": raw_text,
    }


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


def _strip_code_fences(code: str) -> str:
    """Strip triple backticks code block fences if present inside code."""
    fenced = re.match(r"^```(?:[a-zA-Z0-9_\-]+)?\s*\n?(.*?)\n?```$", code.strip(), re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return code

