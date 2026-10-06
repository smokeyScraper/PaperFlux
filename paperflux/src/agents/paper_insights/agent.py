from pathlib import Path
from typing import Any, Optional

from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.agents.paper_insights.parser import parse_insights_payload
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import (
    AGENTS_DIR,
    GEMINI_INSIGHTS_MODEL,
    load_agent_keys,
)

SKILL_PATH = AGENTS_DIR / "paper_insights" / "SKILL.md"


class PaperInsightsAgent:
    """Turns a paper + analyst writeup into 2-3 insights with visual diagrams (SVG/Mermaid)."""

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        skill_path: Path = SKILL_PATH,
    ):
        _, body = load_skill(skill_path)
        self.skill = body
        self.client = client or GeminiClient(
            api_keys=load_agent_keys("GEMINI_INSIGHTS_API_KEY"),
            model=GEMINI_INSIGHTS_MODEL,
        )

    def run(
        self,
        pdf_path: str,
        explanation: str,
        title: str = "",
        paper_id: str = "",
    ) -> dict[str, Any]:
        user_text = (
            "Using the attached research paper PDF and the analyst writeup below, "
            "produce exactly 2 or 3 insights with diagrams as specified in your skill.\n\n"
            f"Paper ID: {paper_id or 'unknown'}\n"
            f"Title: {title or 'See PDF'}\n\n"
            "--- Analyst writeup ---\n"
            f"{explanation}\n"
            "--- End analyst writeup ---\n"
        )
        raw = self.client.generate_from_pdf(
            pdf_path=pdf_path,
            user_text=user_text,
            system_instruction=self.skill,
            temperature=0.3,
        )
        return parse_insights_payload(raw)
