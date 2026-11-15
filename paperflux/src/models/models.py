from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class Paper:
    def __init__(
        self,
        paper_id: str,
        title: str,
        authors: List[Dict],
        summary: str,
        published_at: str,
        explanation: Optional[str] = None,
        insights: Optional[Dict[str, Any]] = None,
        pdf_url: Optional[str] = None,
        upvotes: int = 0,
        media_urls: Optional[List[str]] = None,
        github_repo: Optional[str] = None,
        project_page: Optional[str] = None,
        visual_brief: str = "",
    ):
        self.paper_id = paper_id
        self.title = title
        self.authors = authors
        self.summary = summary
        self.published_at = published_at
        self.explanation = explanation
        self.insights = insights or {"insights": []}
        self.pdf_url = pdf_url
        self.upvotes = upvotes
        self.media_urls = media_urls or []
        self.github_repo = github_repo
        self.project_page = project_page
        self.visual_brief = visual_brief
        self.processed_at = datetime.now(timezone.utc)

    def to_dict(self) -> Dict:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "authors": self.authors,
            "summary": self.summary,
            "published_at": self.published_at,
            "explanation": self.explanation,
            "insights": self.insights,
            "pdf_url": self.pdf_url,
            "upvotes": self.upvotes,
            "media_urls": self.media_urls,
            "github_repo": self.github_repo,
            "project_page": self.project_page,
            "visual_brief": self.visual_brief,
            "processed_at": self.processed_at,
        }


class ProcessingMetadata:
    def __init__(self, last_processed_date: datetime = None):
        self.last_processed_date = last_processed_date or datetime.now(timezone.utc)
        self.is_processing = False
        self.has_run = False  # False until papers were processed at least once
        self.progress_done = 0
        self.progress_total = 0

    def to_dict(self) -> Dict:
        return {
            "last_processed_date": self.last_processed_date,
            "is_processing": self.is_processing,
            "progress_done": self.progress_done,
            "progress_total": self.progress_total,
        }
