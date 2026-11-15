"""Writes the two-host podcast script for one paper from what the other agents produced.

Text only (no PDF): the analyst writeup, its Visual Brief, and the insights pack are already
grounded in the paper, and Flash-Lite's daily quota is far larger than Flash's.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional, Sequence

from paperflux.src.agents.fallback import FallbackLLM
from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.agents.openai_compat_client import OpenAICompatClient
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import (
    AGENTS_DIR,
    FALLBACK_LLM_API_KEYS,
    FALLBACK_LLM_BASE_URL,
    FALLBACK_LLM_MODEL,
    GEMINI_PODCAST_MODELS,
    PODCAST_HOSTS,
    PODCAST_TARGET_WORDS,
    load_podcast_keys,
)
from paperflux.src.podcast.script import parse_script, script_schema

logger = logging.getLogger("paperflux.podcast.writer")
SKILL_PATH = AGENTS_DIR / "paper_podcast" / "SKILL.md"
MAX_WRITEUP_CHARS = 16000
MAX_BRIEF_CHARS = 6000


def build_default_client() -> FallbackLLM:
    clients: list[Any] = [GeminiClient(api_keys=load_podcast_keys(), models=GEMINI_PODCAST_MODELS)]
    if FALLBACK_LLM_BASE_URL and FALLBACK_LLM_API_KEYS:
        clients.append(OpenAICompatClient(FALLBACK_LLM_BASE_URL, FALLBACK_LLM_API_KEYS, FALLBACK_LLM_MODEL))
    return FallbackLLM(clients)


class PodcastWriterAgent:
    def __init__(
        self,
        client: Optional[Any] = None,
        skill_path: Path = SKILL_PATH,
        hosts: Sequence[dict[str, str]] = PODCAST_HOSTS,
        target_words: int = PODCAST_TARGET_WORDS,
    ):
        _, body = load_skill(skill_path)
        self.hosts = hosts
        self.target_words = target_words
        self.skill = (
            body.replace("{{HOST_A}}", hosts[0]["name"])
            .replace("{{HOST_B}}", hosts[1]["name"])
            .replace("{{TARGET_WORDS}}", str(target_words))
        )
        self.client = client or build_default_client()

    def run(self, paper: dict[str, Any]) -> dict[str, Any]:
        """Return a usable script, or raise LLMError / QuotaExhaustedError."""
        user_text = build_user_text(paper)
        raw = self._ask(user_text)
        script, problems = parse_script(raw, self.hosts, self.target_words)
        if script is None:
            logger.info("Podcast script for %s needs a repair pass: %s",
                        paper.get("paper_id"), "; ".join(problems)[:300])
            try:
                script, problems = parse_script(self._ask(build_repair_text(user_text, raw, problems)),
                                                self.hosts, self.target_words)
            except QuotaExhaustedError:
                raise
            except LLMError as exc:
                logger.warning("Podcast repair pass failed: %s", exc)
        if script is None:
            raise LLMError("The podcast script was not usable: " + "; ".join(problems)[:300])
        return script

    def _ask(self, user_text: str) -> str:
        return self.client.generate_text(
            user_text=user_text,
            system_instruction=self.skill,
            temperature=0.7,
            response_schema=script_schema(self.hosts),
            max_output_tokens=8192,
        )


def _cap(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[...truncated...]"


def _pack_notes(insights: dict[str, Any]) -> str:
    """The insights pack as plain notes (the parts worth saying out loud)."""
    lines: list[str] = []
    if insights.get("tldr"):
        lines.append(f"TL;DR: {insights['tldr']}")
    concept = insights.get("core_concept") or {}
    if concept.get("name"):
        lines.append(f"Core idea: {concept['name']}. {concept.get('explanation', '')}")
    if concept.get("analogy"):
        lines.append(f"Analogy: {concept['analogy']}")
    for item in insights.get("key_numbers") or []:
        lines.append(f"Key number: {item.get('label')}: {item.get('value')} ({item.get('context', '')})")
    for item in insights.get("insights") or []:
        lines.append(f"Insight: {item.get('title')}. {item.get('summary', '')} "
                     f"Why it matters: {item.get('why_it_matters', '')} ({item.get('evidence', '')})")
    return "\n".join(lines)


def build_user_text(paper: dict[str, Any]) -> str:
    notes = _pack_notes(paper.get("insights") or {})
    brief = _cap(paper.get("visual_brief") or "", MAX_BRIEF_CHARS)
    return (
        "Write the podcast script for this paper, following your instructions exactly. Use only facts "
        "and numbers from the notes below.\n\n"
        f"Paper ID: {paper.get('paper_id') or 'unknown'}\n"
        f"Title: {paper.get('title') or 'unknown'}\n"
        f"Abstract:\n{paper.get('summary') or '(not provided)'}\n\n"
        + (f"--- Explainer notes ---\n{notes}\n--- End explainer notes ---\n\n" if notes else "")
        + (f"--- Analyst's structured notes ---\n{brief}\n--- End structured notes ---\n\n" if brief else "")
        + f"--- Analyst writeup ---\n{_cap(paper.get('explanation') or '', MAX_WRITEUP_CHARS)}\n"
        "--- End analyst writeup ---\n"
    )


def build_repair_text(user_text: str, previous: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems[:8]) or "- the output was not valid JSON"
    return (
        f"{user_text}\n\nYour previous script could not be recorded.\nProblems found:\n{listed}\n\n"
        f"Previous answer (truncated):\n{(previous or '')[:4000]}\n\n"
        "Return the corrected, complete JSON object now: both hosts speaking in alternation, "
        "18-28 turns, about the target length."
    )
