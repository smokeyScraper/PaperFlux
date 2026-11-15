"""Boot the real app.py (Mongo + scheduler stubbed) and drive it like a visitor."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]

FLOW = {"type": "flow", "title": "How it works",
        "spec": {"title": "How it works", "nodes": [{"id": "a", "label": "Input", "kind": "input"}, {"id": "b", "label": "Model", "kind": "model"}, {"id": "c", "label": "Output", "kind": "output"}],
                 "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}],
                 "steps": [{"caption": "Data goes in", "nodes": ["a"]}, {"caption": "Model runs", "nodes": ["b", "c"]}]}}
CHART = {"type": "chart", "caption": "Table 2",
         "spec": {"kind": "bar", "title": "Accuracy", "series": [{"name": "Ours", "points": [{"label": "A", "value": 9}, {"label": "B", "value": 7}]}]}}
TOKENS = {"type": "tokens", "title": "Draft and verify", "tokens": ["The", {"text": "fox", "state": "hidden"}],
          "steps": [{"caption": "Draft", "changes": [{"index": 1, "state": "new"}]},
                    {"caption": "Verify", "changes": [{"index": 1, "state": "keep"}]}]}
ANALYSIS = "# T\n\n## TL;DR\n- Problem: X is slow.\n- Idea: do Y.\n\n## The Big Idea\nBecause Z.\n\n## Where to Be Skeptical\nOnly one dataset.\n"


def v3_paper(pid="2611.00001", upvotes=42, title="A New Paper"):
    return {
        "paper_id": pid, "title": title, "published_at": "2026-11-03T00:00:00Z", "upvotes": upvotes,
        "authors": [{"name": "A"}, {"name": "B"}], "pdf_url": f"https://arxiv.org/pdf/{pid}", "summary": "The abstract.",
        "explanation": ANALYSIS, "media_urls": [], "github_repo": "https://github.com/x/y",
        "insights": {"schema_version": 3, "tldr": "They did X.", "key_numbers": [{"label": "Acc", "value": "91%", "context": "vs 88"}],
                     "core_concept": {"name": "The Idea", "explanation": "Because.", "analogy": "Like Z.", "visual": FLOW},
                     "insights": [{"title": "Mechanism", "summary": "s", "why_it_matters": "w", "evidence": "Fig 1", "visual": FLOW},
                                  {"title": "Results", "summary": "s", "visual": CHART, "evidence": "Table 2"}]},
    }


PENDING_PAPER = {"paper_id": "2611.00002", "title": "Pending Paper", "published_at": "2026-11-03T00:00:00Z", "upvotes": 10,
                 "authors": [{"name": "C"}], "pdf_url": "https://arxiv.org/pdf/2611.00002", "summary": "Abstract two.",
                 "explanation": ANALYSIS, "insights": {"insights": [], "parse_error": "Daily free-tier quota reached; will retry."}}
LEGACY_PAPER = {"paper_id": "2610.00009", "title": "An Old Paper", "published_at": "2026-10-03T00:00:00Z", "upvotes": 1,
                "authors": [{"name": "B"}], "pdf_url": "https://arxiv.org/pdf/2610.00009", "summary": "Old abstract.", "explanation": "old analysis",
                "insights": {"schema_version": 2, "insights": [
                    {"title": "Legacy svg", "summary": "s", "visual": {"type": "svg", "code": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><script>alert(1)</script><rect width='5' height='5'/></svg>"}},
                    {"title": "Legacy mermaid", "summary": "s", "visual": {"type": "mermaid", "code": "graph TD; A-->B"}}]}}


def boot(papers, query=None, metadata=None):
    from paperflux.src.services import database, scheduler

    client = MagicMock()
    coll = client.__getitem__.return_value.__getitem__.return_value
    coll.find.return_value = list(papers)
    coll.find_one.side_effect = lambda q, *a, **k: metadata if q == {"_id": "processing_metadata"} else None
    coll.count_documents.return_value = len(papers)
    env = {"MONGODB_URI": "mongodb://stub", "GEMINI_ANALYST_API_KEY": "x", "GEMINI_INSIGHTS_API_KEY": "y"}
    st.cache_resource.clear()
    database.DatabaseService._instance = None
    scheduler.PaperScheduler._instance = None
    with patch.dict(os.environ, env), \
         patch.object(database, "MongoClient", return_value=client), \
         patch.object(scheduler.PaperScheduler, "start_scheduler", lambda self: None):
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        for key, value in (query or {}).items():
            at.query_params[key] = value
        at.run()
    database.DatabaseService._instance = None
    scheduler.PaperScheduler._instance = None
    return at


def texts(elements):
    return [e.value for e in elements]


class AppSmokeTest(unittest.TestCase):
    def assert_healthy(self, at):
        self.assertEqual(texts(at.exception), [])
        # Deprecation notices must never reach visitors
        shown = " ".join(texts(at.warning) + texts(at.error)).lower()
        self.assertNotIn("use_container_width", shown)
        self.assertNotIn("will be removed", shown)

    def test_feed_lists_papers_by_upvotes_with_open_buttons(self):
        at = boot([LEGACY_PAPER, v3_paper(upvotes=42), PENDING_PAPER])
        self.assert_healthy(at)
        opens = [b for b in at.button if b.label == "Open →"]
        self.assertEqual([b.key for b in opens], ["open-2611.00001", "open-2611.00002", "open-2610.00009"])  # 42, 10, 1
        md = " ".join(texts(at.markdown))
        self.assertIn("1. A New Paper", md)
        self.assertIn("They did X.", md)  # TL;DR as the blurb
        self.assertIn("Animated explainer", md)
        self.assertIn("Diagrams coming soon", md)
        self.assertIn("Problem: X is slow.", md)  # pending paper falls back to the analysis TL;DR

    def test_clicking_a_card_opens_the_paper_and_updates_the_url(self):
        at = boot([v3_paper()])
        at.button(key="open-2611.00001").click().run()
        self.assert_healthy(at)
        self.assertEqual(at.query_params["paper"], "2611.00001")
        self.assertTrue(any("A New Paper" in m for m in texts(at.markdown)))
        self.assertTrue(any("Paper 1 of 1" in c for c in texts(at.caption)))

    def test_deep_link_renders_overview_with_pack(self):
        at = boot([v3_paper()], query={"paper": "2611.00001"})
        self.assert_healthy(at)
        self.assertTrue(any("They did X." in i for i in texts(at.info)))
        self.assertEqual(texts(at.metric), ["91%"])
        self.assertEqual(at.get("segmented_control")[0].value, "Overview")

    def test_tabs_are_ordered_abstract_first_but_overview_stays_the_default(self):
        at = boot([v3_paper()], query={"paper": "2611.00001"})
        control = at.get("segmented_control")[0]
        self.assertEqual(list(control.options), ["Abstract", "Overview", "Insights", "Analysis"])
        self.assertEqual(control.value, "Overview")

    def test_abstract_view_shows_the_authors_abstract(self):
        at = boot([v3_paper()], query={"paper": "2611.00001", "view": "abstract"})
        self.assert_healthy(at)
        self.assertEqual(at.get("segmented_control")[0].value, "Abstract")
        self.assertIn("The abstract.", texts(at.markdown))

    def test_theme_toggle_writes_the_stored_theme_and_reloads(self):
        at = boot([v3_paper()])
        self.assert_healthy(at)
        toggle = at.button(key="theme-toggle")
        self.assertEqual(toggle.label, "Dark mode")  # AppTest reports a light theme
        self.assertEqual(at.get("html"), [])
        toggle.click().run()
        self.assert_healthy(at)
        script = " ".join(h.proto.body for h in at.get("html"))
        self.assertIn("stActiveTheme-", script)
        self.assertIn("location.reload()", script)
        # The script must not be a sibling of the toggle: that flex item is what shoved it left.
        self.assertIn("paperflux-theme-switch", script)
        self.assertIn("display:none", script)

    def test_view_param_selects_the_tab_and_renders_only_that_view(self):
        at = boot([v3_paper()], query={"paper": "2611.00001", "view": "insights"})
        self.assert_healthy(at)
        self.assertEqual(at.get("segmented_control")[0].value, "Insights")
        self.assertTrue(any("Where in the paper: Table 2" in c for c in texts(at.caption)))
        self.assertEqual(texts(at.metric), [])  # Overview content is NOT rendered (lazy views)

    def test_token_animation_renders_and_charts_offer_a_gif(self):
        paper = v3_paper()
        paper["insights"]["insights"].append({"title": "Tokens", "summary": "s", "visual": {"type": "tokens", "spec": TOKENS}})
        at = boot([paper], query={"paper": "2611.00001", "view": "insights"})
        self.assert_healthy(at)
        self.assertTrue(any("3. Tokens" in m for m in texts(at.markdown)))
        chart_gif = next(b for b in at.button if b.label == "🎞️ Make GIF" and b.key.startswith("gif-2611.00001-i2-"))
        chart_gif.click().run()
        self.assert_healthy(at)
        self.assertEqual(len(at.get("download_button")), 2)  # the analysis download + the new GIF download

    def test_analysis_view_is_sectioned_with_reading_time(self):
        at = boot([v3_paper()], query={"paper": "2611.00001", "view": "analysis"})
        self.assert_healthy(at)
        self.assertEqual([e.label for e in at.expander], ["TL;DR", "The Big Idea", "Where to Be Skeptical"])
        self.assertTrue(any("min read" in c for c in texts(at.caption)))

    def test_unknown_or_stale_link_falls_back_to_the_feed(self):
        at = boot([v3_paper()], query={"paper": "does-not-exist"})
        self.assert_healthy(at)
        self.assertTrue(any("no longer available" in w for w in texts(at.warning)))
        self.assertTrue(any(b.label == "Open →" for b in at.button))

    def test_pending_paper_shows_a_useful_overview_instead_of_an_error(self):
        at = boot([PENDING_PAPER], query={"paper": "2611.00002"})
        self.assert_healthy(at)
        self.assertTrue(any("still being prepared" in i for i in texts(at.info)))
        md = " ".join(texts(at.markdown))
        self.assertIn("Problem: X is slow.", md)
        self.assertIn("Because Z.", md)
        self.assertTrue(any(b.label.startswith("Read the full analysis") for b in at.button))
        insights = boot([PENDING_PAPER], query={"paper": "2611.00002", "view": "insights"})
        self.assert_healthy(insights)
        self.assertTrue(any("still being prepared" in i for i in texts(insights.info)))

    def test_legacy_paper_still_renders_and_unsafe_svg_is_stripped(self):
        at = boot([LEGACY_PAPER], query={"paper": "2610.00009", "view": "insights"})
        self.assert_healthy(at)
        self.assertTrue(any("Legacy svg" in m for m in texts(at.markdown)))

    def test_empty_site_shows_a_friendly_state(self):
        at = boot([])
        self.assert_healthy(at)
        md = " ".join(texts(at.markdown))
        self.assertIn("No papers yet", md)

    def test_processing_shows_live_progress_and_a_preparing_state(self):
        meta = {"is_processing": True, "processing_started_at": datetime.now(timezone.utc) - timedelta(minutes=5),
                "progress_done": 3, "progress_total": 10, "last_processed_date": datetime.now(timezone.utc) - timedelta(days=1)}
        at = boot([], metadata=meta)
        self.assert_healthy(at)
        self.assertIn("Preparing today's papers", " ".join(texts(at.markdown)))
        bars = [e for e in at.sidebar if getattr(e, "type", "") == "progress"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].value, 30)  # 3 of 10 papers
        self.assertIn("3/10", bars[0].proto.text)
        self.assertFalse(any(b.label == "Process papers now" for b in at.sidebar.button))  # no double-start

    def test_stale_processing_flag_does_not_trap_the_ui_in_processing(self):
        meta = {"is_processing": True, "processing_started_at": datetime.now(timezone.utc) - timedelta(hours=6),
                "progress_done": 2, "progress_total": 10, "last_processed_date": datetime.now(timezone.utc) - timedelta(days=2)}
        at = boot([v3_paper()], metadata=meta)
        self.assert_healthy(at)
        self.assertEqual([e for e in at.sidebar if getattr(e, "type", "") == "progress"], [])
        self.assertTrue(any(b.label in {"Process papers now", "Resume processing"} for b in at.sidebar.button))

    def test_idle_sidebar_offers_manual_run_and_states_next_update(self):
        at = boot([], metadata=None)
        self.assert_healthy(at)
        self.assertTrue(any(b.label == "Process papers now" for b in at.sidebar.button))
        self.assertTrue(any("Next update" in c or "next update" in c for c in texts(at.sidebar.caption)))

    def test_quota_pending_papers_are_explained_in_the_sidebar(self):
        at = boot([PENDING_PAPER, v3_paper()])
        self.assert_healthy(at)
        self.assertTrue(any("waiting for free-tier" in i for i in texts(at.sidebar.info)))

    def test_navigation_between_papers(self):
        at = boot([v3_paper("2611.00001", 42), v3_paper("2611.00003", 7, "Second Paper")], query={"paper": "2611.00001"})
        self.assert_healthy(at)
        nxt = [b for b in at.button if b.key == "nav-next"][0]
        prev = [b for b in at.button if b.key == "nav-prev"][0]
        self.assertFalse(nxt.disabled)
        self.assertTrue(prev.disabled)
        nxt.click().run()
        self.assertEqual(at.query_params["paper"], "2611.00003")
        self.assertTrue(any("Second Paper" in m for m in texts(at.markdown)))


if __name__ == "__main__":
    unittest.main()
