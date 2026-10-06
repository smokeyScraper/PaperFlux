from pathlib import Path
from typing import Optional

from paperflux.src.agents.gemini_client import GeminiClient
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import (
    AGENTS_DIR,
    GEMINI_ANALYST_MODEL,
    load_agent_keys,
)

SKILL_PATH = AGENTS_DIR / "paper_analyst" / "SKILL.md"


class PaperAnalystAgent:
    """Reads a paper PDF and produces a deep technical explanation."""

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        skill_path: Path = SKILL_PATH,
    ):
        _, body = load_skill(skill_path)
        self.skill = body
        self.client = client or GeminiClient(
            api_keys=load_agent_keys("GEMINI_ANALYST_API_KEY"),
            model=GEMINI_ANALYST_MODEL,
        )

    def run(
        self,
        pdf_path: str,
        title: str = "",
        summary: str = "",
        paper_id: str = "",
    ) -> str:
        user_text = (
            "Analyze the attached research paper PDF using your skill instructions.\n\n"
            f"Paper ID: {paper_id or 'unknown'}\n"
            f"Title: {title or 'See PDF'}\n"
            f"Abstract/summary from the listing:\n{summary or '(not provided)'}\n"
        )
        return self.client.generate_from_pdf(
            pdf_path=pdf_path,
            user_text=user_text,
            system_instruction=self.skill,
            temperature=0.2,
        )
