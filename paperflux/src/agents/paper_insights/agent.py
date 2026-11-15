import logging
from pathlib import Path
from typing import Any, Optional

from paperflux.src.agents.fallback import FallbackLLM
from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.agents.llm_errors import LLMError, QuotaExhaustedError
from paperflux.src.agents.openai_compat_client import OpenAICompatClient
from paperflux.src.agents.paper_insights.parser import parse_insights_payload
from paperflux.src.agents.paper_insights.plan import (
    PLAN_SCHEMA,
    is_usable,
    parse_plan,
    plan_score,
)
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import (
    AGENTS_DIR,
    FALLBACK_LLM_API_KEYS,
    FALLBACK_LLM_BASE_URL,
    FALLBACK_LLM_MODEL,
    GEMINI_INSIGHTS_MODELS,
    INSIGHTS_ATTACH_PDF,
    load_agent_keys,
)

logger = logging.getLogger("paperflux.insights")
SKILL_PATH = AGENTS_DIR / "paper_insights" / "SKILL.md"
MAX_WRITEUP_CHARS = 24000
MAX_BRIEF_CHARS = 8000


def build_default_client() -> FallbackLLM:
    """Gemini model chain first; an optional OpenAI-compatible provider after it."""
    clients: list[Any] = [
        GeminiClient(
            api_keys=load_agent_keys("GEMINI_INSIGHTS_API_KEY"),
            models=GEMINI_INSIGHTS_MODELS,
        )
    ]
    if FALLBACK_LLM_BASE_URL and FALLBACK_LLM_API_KEYS:
        clients.append(
            OpenAICompatClient(FALLBACK_LLM_BASE_URL, FALLBACK_LLM_API_KEYS, FALLBACK_LLM_MODEL)
        )
    return FallbackLLM(clients)


class PaperInsightsAgent:
    """Turns a paper + analyst writeup into an explainer pack (TL;DR, core concept
    animation, key numbers, 2-3 insights with flow diagrams / charts)."""

    def __init__(
        self,
        client: Optional[Any] = None,
        skill_path: Path = SKILL_PATH,
        attach_pdf: bool = INSIGHTS_ATTACH_PDF,
    ):
        _, body = load_skill(skill_path)
        self.skill = body
        self.client = client or build_default_client()
        self.attach_pdf = attach_pdf

    def run(
        self,
        pdf_path: str,
        explanation: str,
        title: str = "",
        paper_id: str = "",
        abstract: str = "",
        visual_brief: str = "",
    ) -> dict[str, Any]:
        user_text = build_user_text(title, paper_id, abstract, explanation, visual_brief)
        raw = self._ask(user_text, pdf_path)
        doc, problems = parse_plan(raw)

        if not is_usable(doc):
            logger.info("Insights for %s need a repair pass: %s", paper_id, "; ".join(problems)[:300])
            try:
                raw2 = self._ask_text(build_repair_text(user_text, raw, problems))
                doc2, _ = parse_plan(raw2)
                if plan_score(doc2) >= plan_score(doc):
                    doc, raw = doc2, raw2
            except QuotaExhaustedError:
                raise
            except LLMError as exc:
                logger.warning("Repair pass failed for %s: %s", paper_id, exc)

        if doc is None:  # model ignored the JSON contract: salvage Markdown sections
            legacy = parse_insights_payload(raw)
            legacy["raw"] = raw
            return legacy
        return doc

    # ---- model calls -----------------------------------------------------
    def _kwargs(self, user_text: str) -> dict[str, Any]:
        return {
            "user_text": user_text,
            "system_instruction": self.skill,
            "temperature": 0.3,
            "response_schema": PLAN_SCHEMA,
            "max_output_tokens": 16384,
        }

    def _ask(self, user_text: str, pdf_path: str) -> str:
        if self.attach_pdf and pdf_path and getattr(self.client, "can_read_pdf", True):
            try:
                return self.client.generate_from_pdf(pdf_path=pdf_path, **self._kwargs(user_text))
            except QuotaExhaustedError:
                logger.info("Gemini quota spent; trying text-only providers for insights")
            except LLMError as exc:
                logger.warning("PDF-grounded insights call failed (%s); retrying text-only", str(exc)[:160])
        return self.client.generate_text(**self._kwargs(user_text))

    def _ask_text(self, user_text: str) -> str:
        return self.client.generate_text(**self._kwargs(user_text))


def _cap(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[...truncated...]"


def build_user_text(
    title: str, paper_id: str, abstract: str, explanation: str, visual_brief: str = ""
) -> str:
    brief = _cap(visual_brief, MAX_BRIEF_CHARS)
    brief_block = (
        "--- Visual brief (the analyst's structured notes for you) ---\n"
        f"{brief}\n"
        "--- End visual brief ---\n\n"
        if brief
        else ""
    )
    return (
        "Create the explainer pack for this paper, following your instructions exactly.\n"
        "Ground every node, number and claim in the paper (the PDF when attached, otherwise "
        "the analyst's notes). Never invent components or results.\n\n"
        f"Paper ID: {paper_id or 'unknown'}\n"
        f"Title: {title or 'See PDF'}\n"
        f"Abstract:\n{abstract or '(not provided)'}\n\n"
        f"{brief_block}"
        "--- Analyst writeup ---\n"
        f"{_cap(explanation, MAX_WRITEUP_CHARS)}\n"
        "--- End analyst writeup ---\n"
    )


def build_repair_text(user_text: str, previous: str, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems[:8]) or "- the output was not valid JSON"
    return (
        f"{user_text}\n\nYour previous answer could not be used.\n"
        f"Problems found:\n{listed}\n\n"
        "Previous answer (truncated):\n"
        f"{(previous or '')[:5000]}\n\n"
        "Return the corrected, complete JSON object now. Every visual needs at least 3 nodes "
        "(flow) or at least 2 numeric points (chart); there must be 2 or 3 insights."
    )
