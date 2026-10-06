from datetime import datetime
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
    ):
        self.paper_id = paper_id
        self.title = title
        self.authors = authors
        self.summary = summary
        self.published_at = published_at
        self.explanation = explanation
        self.insights = insights or {"insights": []}
        self.pdf_url = pdf_url
        self.processed_at = datetime.utcnow()

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
            "processed_at": self.processed_at,
        }


class ProcessingMetadata:
    def __init__(self, last_processed_date: datetime = None):
        self.last_processed_date = last_processed_date or datetime.utcnow()
        self.is_processing = False

    def to_dict(self) -> Dict:
        return {
            "last_processed_date": self.last_processed_date,
            "is_processing": self.is_processing,
        }
