import asyncio
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from paperflux.src.agents.llm_errors import QuotaExhaustedError
from paperflux.src.models.models import Paper
from paperflux.src.services import paper_processor as pp
from paperflux.src.services.database import paper_is_complete
from paperflux.src.services.paper_fetcher import PaperFetcher

V3 = {"schema_version": 3, "insights": [{"title": "t"}], "tldr": "x"}


def entry(pid, upvotes=0):
    return {"paper": {"id": pid, "title": f"T{pid}", "summary": "abs", "authors": [], "publishedAt": "2026-11-01T00:00:00Z", "upvotes": upvotes}}


class FakeCollection:
    def __init__(self, db):
        self.db = db

    def find_one(self, query):
        return self.db.docs.get(query["paper_id"])


class FakeDB:
    def __init__(self, docs=None):
        self.docs = dict(docs or {})
        self.collection = FakeCollection(self)
        self.processed_marked = 0
        self.pruned = None
        self.status = []

    def upsert_paper(self, paper: Paper):
        self.docs[paper.paper_id] = paper.to_dict()

    def has_complete_paper(self, pid):
        return paper_is_complete(self.docs.get(pid))

    def get_paper_by_id(self, pid):
        return self.docs.get(pid)

    def delete_papers_not_in(self, keep):
        self.pruned = set(keep)
        for pid in [p for p in self.docs if p not in keep]:
            del self.docs[pid]

    def update_last_processed_date(self):
        self.processed_marked += 1

    def set_processing_status(self, flag):
        self.status.append(flag)


class FakeFetcher:
    def __init__(self, papers, fail=False):
        self.papers, self.fail = papers, fail
        self.downloaded = []
        self.select_top = PaperFetcher.select_top
        self.parse_paper_data = PaperFetcher().parse_paper_data

    async def fetch_papers(self):
        if self.fail:
            raise RuntimeError("HF API down")
        return self.papers

    async def download_papers(self, papers):
        self.downloaded = [p["paper"]["id"] for p in papers]
        paths = {}
        for p in papers:
            f = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            f.write(b"%PDF")
            f.close()
            paths[p["paper"]["id"]] = f.name
        return paths


class FakeAnalyst:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def run(self, pdf_path, title="", summary="", paper_id=""):
        self.calls.append(paper_id)
        if self.error:
            raise self.error
        return f"analysis of {paper_id}"


class FakeInsights:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def run(self, pdf_path, explanation, title="", paper_id="", abstract=""):
        self.calls.append(paper_id)
        if self.error:
            raise self.error
        return dict(V3)


def make_processor(db, fetcher, analyst, insights):
    proc = pp.PaperProcessor.__new__(pp.PaperProcessor)
    proc.fetcher, proc.db = fetcher, db
    proc._analyst, proc._insights_agent = analyst, insights
    proc._running = False
    proc._quota_event = threading.Event()
    return proc


def run(proc):
    return asyncio.run(proc.process_papers())


class ProcessorTest(unittest.TestCase):
    def test_fetch_failure_does_not_wipe_existing_papers(self):
        db = FakeDB({"old": {"paper_id": "old", "explanation": "e", "insights": V3}})
        proc = make_processor(db, FakeFetcher([], fail=True), FakeAnalyst(), FakeInsights())
        self.assertFalse(run(proc))
        self.assertIn("old", db.docs)  # the bug this guards against: clear-before-fetch
        self.assertEqual(db.processed_marked, 0)
        self.assertEqual(db.status[-1], False)  # processing flag always released

    def test_only_top_n_by_upvotes_are_processed_and_old_ones_pruned(self):
        papers = [entry("a", 1), entry("b", 50), entry("c", 10), entry("d", 99)]
        db = FakeDB({"yesterday": {"paper_id": "yesterday", "explanation": "e", "insights": V3}})
        analyst, insights = FakeAnalyst(), FakeInsights()
        proc = make_processor(db, FakeFetcher(papers), analyst, insights)
        with patch.object(pp, "MAX_PAPERS", 2):
            self.assertTrue(run(proc))
        self.assertEqual(set(db.docs), {"d", "b"})
        self.assertEqual(sorted(analyst.calls), ["b", "d"])
        self.assertEqual(db.processed_marked, 1)

    def test_complete_papers_are_skipped_without_model_calls(self):
        db = FakeDB({"a": {"paper_id": "a", "explanation": "e", "insights": V3}})
        fetcher, analyst, insights = FakeFetcher([entry("a")]), FakeAnalyst(), FakeInsights()
        proc = make_processor(db, fetcher, analyst, insights)
        self.assertTrue(run(proc))
        self.assertEqual((analyst.calls, insights.calls, fetcher.downloaded), ([], [], []))

    def test_insights_quota_stores_analysis_keeps_day_open_then_resumes(self):
        db = FakeDB()
        analyst = FakeAnalyst()
        proc = make_processor(db, FakeFetcher([entry("a")]), analyst, FakeInsights(QuotaExhaustedError("spent")))
        self.assertTrue(run(proc))
        self.assertEqual(db.docs["a"]["explanation"], "analysis of a")
        self.assertEqual(db.docs["a"]["insights"]["insights"], [])
        self.assertEqual(db.processed_marked, 0)  # day stays open: the hourly tick will retry

        proc2 = make_processor(db, FakeFetcher([entry("a")]), analyst, FakeInsights())
        self.assertTrue(run(proc2))
        self.assertEqual(analyst.calls, ["a"])  # analysis reused, NOT paid for twice
        self.assertTrue(db.has_complete_paper("a"))
        self.assertEqual(db.processed_marked, 1)

    def test_analyst_quota_stops_new_work_and_stores_nothing(self):
        db = FakeDB()
        analyst = FakeAnalyst(QuotaExhaustedError("spent"))
        insights = FakeInsights()
        proc = make_processor(db, FakeFetcher([entry("a"), entry("b")]), analyst, insights)
        self.assertTrue(run(proc))
        self.assertEqual(db.docs, {})
        self.assertEqual(insights.calls, [])
        self.assertEqual(db.processed_marked, 0)

    def test_ordinary_failure_does_not_block_closing_the_day(self):
        db = FakeDB()
        proc = make_processor(db, FakeFetcher([entry("a")]), FakeAnalyst(RuntimeError("bad pdf")), FakeInsights())
        self.assertTrue(run(proc))
        self.assertEqual(db.processed_marked, 1)  # do not retry a broken paper every hour

    def test_temp_pdfs_are_cleaned_up(self):
        db = FakeDB()
        fetcher = FakeFetcher([entry("a")])
        proc = make_processor(db, fetcher, FakeAnalyst(), FakeInsights())
        paths = []
        original = fetcher.download_papers

        async def spy(papers):
            result = await original(papers)
            paths.extend(result.values())
            return result

        fetcher.download_papers = spy
        run(proc)
        self.assertTrue(paths and not any(os.path.exists(p) for p in paths))


class HelpersTest(unittest.TestCase):
    def test_paper_is_complete(self):
        self.assertFalse(paper_is_complete(None))
        self.assertFalse(paper_is_complete({"explanation": "e", "insights": {"schema_version": 2, "insights": [1]}}))
        self.assertFalse(paper_is_complete({"explanation": "e", "insights": {"schema_version": 3, "insights": []}}))
        self.assertFalse(paper_is_complete({"explanation": "", "insights": V3}))
        self.assertTrue(paper_is_complete({"explanation": "e", "insights": V3}))

    def test_media_urls_are_filtered(self):
        e = {"mediaUrls": ["https://cdn/x.gif", "http://insecure/x.gif", "https://cdn/page.html", "https://cdn/v.mp4?x=1", 5],
             "paper": {"id": "1"}}
        self.assertEqual(PaperFetcher.clean_media_urls(e), ["https://cdn/x.gif", "https://cdn/v.mp4?x=1"])


if __name__ == "__main__":
    unittest.main()
