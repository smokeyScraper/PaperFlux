"""Boot the real app.py (Mongo + scheduler stubbed) and render stored papers."""
import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]

FLOW = {"type": "flow", "title": "How it works",
        "spec": {"title": "How it works", "nodes": [{"id": "a", "label": "Input", "kind": "input"}, {"id": "b", "label": "Model", "kind": "model"}, {"id": "c", "label": "Output", "kind": "output"}],
                 "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
                 "steps": [{"caption": "Data goes in", "nodes": ["a"]}, {"caption": "Model runs", "nodes": ["b", "c"]}]}}
CHART = {"type": "chart", "caption": "Table 2",
         "spec": {"kind": "bar", "title": "Accuracy", "series": [{"name": "Ours", "points": [{"label": "A", "value": 9}, {"label": "B", "value": 7}]}]}}

V3_PAPER = {
    "paper_id": "2611.00001", "title": "A New Paper", "published_at": "2026-11-03T00:00:00Z", "upvotes": 42,
    "authors": [{"name": "A"}], "pdf_url": "https://arxiv.org/pdf/2611.00001", "summary": "The abstract.",
    "explanation": "# Long analysis", "media_urls": ["https://example.com/demo.gif"], "github_repo": "https://github.com/x/y",
    "insights": {"schema_version": 3, "tldr": "They did X.", "key_numbers": [{"label": "Acc", "value": "91%", "context": "vs 88"}],
                 "core_concept": {"name": "The Idea", "explanation": "Because.", "analogy": "Like Z.", "visual": FLOW},
                 "insights": [{"title": "Mechanism", "summary": "s", "why_it_matters": "w", "evidence": "Fig 1", "visual": FLOW},
                              {"title": "Results", "summary": "s", "visual": CHART, "evidence": "Table 2"}]},
}
LEGACY_PAPER = {
    "paper_id": "2610.00009", "title": "An Old Paper", "published_at": "2026-10-03T00:00:00Z",
    "authors": [{"name": "B"}], "pdf_url": "https://arxiv.org/pdf/2610.00009", "summary": "Old abstract.", "explanation": "old analysis",
    "insights": {"schema_version": 2, "insights": [
        {"title": "Legacy svg", "summary": "s", "visual": {"type": "svg", "code": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><script>alert(1)</script><rect width='5' height='5'/></svg>"}},
        {"title": "Legacy mermaid", "summary": "s", "visual": {"type": "mermaid", "code": "graph TD; A-->B"}}]},
}


def boot(paper):
    from paperflux.src.services import database, scheduler

    env = {"MONGODB_URI": "mongodb://stub", "GEMINI_ANALYST_API_KEY": "x", "GEMINI_INSIGHTS_API_KEY": "y"}
    client = MagicMock()
    coll = client.__getitem__.return_value.__getitem__.return_value
    coll.find.return_value = [paper]
    coll.find_one.return_value = None
    coll.count_documents.return_value = 1
    database.DatabaseService._instance = None
    scheduler.PaperScheduler._instance = None
    with patch.dict(os.environ, env), \
         patch.object(database, "MongoClient", return_value=client), \
         patch.object(scheduler.PaperScheduler, "start_scheduler", lambda self: None):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    database.DatabaseService._instance = None
    scheduler.PaperScheduler._instance = None
    return at


class AppSmokeTest(unittest.TestCase):
    def test_new_explainer_pack_renders_with_overview(self):
        at = boot(V3_PAPER)
        self.assertEqual([e.value for e in at.exception], [])
        labels = [t.label for t in at.tabs]
        self.assertIn("Overview", labels)
        self.assertIn("Insights & Diagrams", labels)
        self.assertTrue(any("They did X." in i.value for i in at.info))
        self.assertEqual([m.value for m in at.metric], ["91%"])
        self.assertTrue(any("The Idea" in m.value for m in at.markdown))
        self.assertTrue(any("Where in the paper: Table 2" in c.value for c in at.caption))

    def test_legacy_papers_still_render_and_unsafe_svg_is_stripped(self):
        at = boot(LEGACY_PAPER)
        self.assertEqual([e.value for e in at.exception], [])
        self.assertNotIn("Overview", [t.label for t in at.tabs])
        self.assertTrue(any("Legacy svg" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
