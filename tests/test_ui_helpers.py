import unittest
from datetime import datetime, timedelta, timezone

from paperflux.src.services.schedule_rules import is_due, is_stale, next_scheduled_run
from paperflux.src.ui import content as c
from paperflux.src.ui.timeutil import format_local, humanize_delta, relative, to_local

UTC = timezone.utc
ANALYSIS = """# Title of paper

## TL;DR
- Problem: models are **huge**.
- Idea: low-rank [updates](http://x).
- Result: 0.01% params (Table 1).

## The Big Idea
Freeze W, learn BA.

```python
## not a heading inside a fence
x = 1
```

## Where to Be Skeptical
Only GLUE tested.
"""


def paper(pid, upvotes=0, published="2026-11-03T00:00:00Z", **kw):
    return {"paper_id": pid, "title": f"T{pid}", "upvotes": upvotes, "published_at": published, **kw}


class SortAndNavTest(unittest.TestCase):
    def test_sorted_by_upvotes_then_recency(self):
        papers = [paper("a", 5), paper("b", 50), paper("c", 5, "2026-11-04T00:00:00Z"), paper("d", 0)]
        self.assertEqual([p["paper_id"] for p in c.sort_papers(papers)], ["b", "c", "a", "d"])

    def test_sort_tolerates_missing_fields(self):
        papers = [{"paper_id": "x", "title": "Z"}, {"paper_id": "y", "title": "A", "upvotes": None, "published_at": "garbage"}]
        self.assertEqual(len(c.sort_papers(papers)), 2)

    def test_neighbors(self):
        ps = [paper("a"), paper("b"), paper("c")]
        self.assertEqual(c.neighbors(ps, "a"), (None, "b", 1, 3))
        self.assertEqual(c.neighbors(ps, "b"), ("a", "c", 2, 3))
        self.assertEqual(c.neighbors(ps, "c"), ("b", None, 3, 3))
        self.assertEqual(c.neighbors(ps, "zzz"), (None, None, 0, 3))

    def test_share_url(self):
        self.assertEqual(c.share_url("https://x.hf.space", "2611.1"), "https://x.hf.space/?paper=2611.1")
        self.assertEqual(c.share_url("https://x.hf.space/", "p", "insights"), "https://x.hf.space/?paper=p&view=insights")
        self.assertEqual(c.share_url("", "p"), "?paper=p")

    def test_short_authors(self):
        self.assertEqual(c.short_authors([{"name": "A"}, {"name": "B"}]), "A, B")
        self.assertEqual(c.short_authors([{"name": n} for n in "ABCD"]), "A, B, C et al.")
        self.assertEqual(c.short_authors([]), "")


class StatusAndBlurbTest(unittest.TestCase):
    PACK = {"schema_version": 3, "insights": [{"title": "t"}], "tldr": "They did **X** and won."}

    def test_status_classification(self):
        self.assertEqual(c.paper_status({"explanation": "e", "insights": self.PACK}), c.EXPLAINER)
        self.assertEqual(c.paper_status({"explanation": "e", "insights": {"insights": [], "parse_error": "quota"}}), c.PENDING)
        self.assertEqual(c.paper_status({"explanation": "e"}), c.PENDING)
        self.assertEqual(c.paper_status({"explanation": "e", "insights": {"schema_version": 2, "insights": [1]}}), c.ANALYSIS_ONLY)
        self.assertEqual(c.paper_status({"insights": self.PACK}), c.EXPLAINER)
        self.assertEqual(c.paper_status({}), c.EMPTY)

    def test_blurb_prefers_tldr_then_analysis_then_abstract(self):
        self.assertEqual(c.blurb({"insights": self.PACK, "summary": "abs"}), "They did X and won.")
        text = c.blurb({"explanation": ANALYSIS, "summary": "abs"})
        self.assertTrue(text.startswith("Problem: models are huge."))
        self.assertNotIn("*", text)
        self.assertNotIn("http", text)
        self.assertEqual(c.blurb({"summary": "Just an abstract."}), "Just an abstract.")

    def test_blurb_is_clipped_on_a_word_boundary(self):
        text = c.blurb({"summary": "word " * 200}, limit=50)
        self.assertLessEqual(len(text), 51)
        self.assertTrue(text.endswith("…"))


class MarkdownTest(unittest.TestCase):
    def test_sections_split_on_h2_and_respect_code_fences(self):
        sections = c.split_sections(ANALYSIS)
        self.assertEqual([t for t, _ in sections], ["TL;DR", "The Big Idea", "Where to Be Skeptical"])
        self.assertIn("not a heading inside a fence", sections[1][1])

    def test_find_section_and_reading_time(self):
        self.assertIn("Only GLUE", c.find_section(ANALYSIS, "skeptical"))
        self.assertEqual(c.find_section(ANALYSIS, "nope"), "")
        self.assertEqual(c.reading_minutes("word " * 660), 3)
        self.assertEqual(c.reading_minutes(""), 1)

    def test_no_headings_gives_no_sections(self):
        self.assertEqual(c.split_sections("just text"), [])

    def test_a_cited_figure_follows_the_paragraph_that_names_it(self):
        sections = [
            ("How It Works", "The encoder is ordinary.\n\nFig. 1 shows the architecture. The decoder follows."),
            ("What the Experiments Show", "Accuracy is in Fig. 2. A later note mentions Fig. 1 again."),
        ]
        figures = [
            {"label": "Figure 1", "caption": "The architecture."},
            {"label": "Figure 2", "caption": "Accuracy on three benchmarks."},
            {"label": "Figure 1a", "caption": "A subfigure the text names separately."},
        ]
        woven = c.weave_figures(sections, figures)
        how, experiments = woven
        self.assertEqual(how[1][0], "The encoder is ordinary.")
        self.assertEqual(how[1][1], "Fig. 1 shows the architecture. The decoder follows.")
        self.assertEqual(how[1][2]["label"], "Figure 1")
        self.assertEqual(experiments[1][1]["label"], "Figure 2")
        shown = [block for block in how[1] + experiments[1] if isinstance(block, dict)]
        self.assertEqual([block["label"] for block in shown if block["label"] == "Figure 1"], ["Figure 1"])

    def test_an_unnamed_figure_lands_in_the_matching_section(self):
        sections = [
            ("How It Works", "Data flows through the encoder."),
            ("What the Experiments Show", "The table lists accuracy."),
            ("Glossary", "Encoder: the first module."),
        ]
        figures = [
            {"label": "Figure 4", "caption": "Overview of the model architecture."},
            {"label": "Figure 9", "caption": "Ablation accuracy against the baseline."},
        ]
        woven = dict(c.weave_figures(sections, figures))
        self.assertEqual(woven["How It Works"][-1]["label"], "Figure 4")
        self.assertEqual(woven["What the Experiments Show"][-1]["label"], "Figure 9")
        self.assertTrue(all(isinstance(block, str) for block in woven["Glossary"]))


class ScheduleRulesTest(unittest.TestCase):
    def dt(self, y, m, d, h=0):
        return datetime(y, m, d, h, 0, tzinfo=UTC)

    def test_friday_evening_next_run_is_monday_not_tomorrow(self):
        fri_evening = self.dt(2026, 11, 13, 20)  # Friday
        self.assertEqual(fri_evening.weekday(), 4)
        nxt = next_scheduled_run(fri_evening, last_processed=self.dt(2026, 11, 13, 8))
        self.assertEqual(nxt, self.dt(2026, 11, 16, 8))  # Monday

    def test_weekend_and_early_morning(self):
        self.assertEqual(next_scheduled_run(self.dt(2026, 11, 14, 12)), self.dt(2026, 11, 16, 8))  # Saturday
        self.assertEqual(next_scheduled_run(self.dt(2026, 11, 16, 5), self.dt(2026, 11, 13, 8)), self.dt(2026, 11, 16, 8))

    def test_due_when_past_schedule_and_not_processed_today(self):
        now = self.dt(2026, 11, 16, 10)  # Monday 10:00
        self.assertTrue(is_due(now, self.dt(2026, 11, 13, 8)))
        self.assertEqual(next_scheduled_run(now, self.dt(2026, 11, 13, 8)), now)  # "due now"
        self.assertFalse(is_due(now, self.dt(2026, 11, 16, 8)))
        self.assertEqual(next_scheduled_run(now, self.dt(2026, 11, 16, 8)), self.dt(2026, 11, 17, 8))

    def test_never_due_on_weekends_or_before_8(self):
        self.assertFalse(is_due(self.dt(2026, 11, 14, 12), None))
        self.assertFalse(is_due(self.dt(2026, 11, 16, 7), None))

    def test_stale_processing_flag(self):
        now = self.dt(2026, 11, 16, 12)
        self.assertTrue(is_stale(now - timedelta(hours=4), now))
        self.assertFalse(is_stale(now - timedelta(minutes=30), now))
        self.assertFalse(is_stale(None, now))

    def test_naive_datetimes_are_treated_as_utc(self):
        naive = datetime(2026, 11, 13, 8, 0)
        self.assertFalse(is_due(datetime(2026, 11, 13, 9, 0), naive))


class TimeUtilTest(unittest.TestCase):
    def test_humanize(self):
        h = humanize_delta
        self.assertEqual(h(timedelta(seconds=10)), "in a moment")
        self.assertEqual(h(timedelta(seconds=-10)), "just now")
        self.assertEqual(h(timedelta(minutes=-5)), "5 min ago")
        self.assertEqual(h(timedelta(hours=3, minutes=20)), "in 3h 20m")
        self.assertEqual(h(timedelta(hours=-2)), "2h ago")
        self.assertEqual(h(timedelta(days=3)), "in 3 days")
        self.assertEqual(h(timedelta(days=-2)), "2 days ago")

    def test_relative_and_local_formatting(self):
        now = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
        self.assertEqual(relative(now - timedelta(hours=1), now), "1h ago")
        ts = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
        self.assertEqual(format_local(ts, "Asia/Kolkata"), "Mon 16 Nov, 13:30 (Asia/Kolkata)")
        self.assertEqual(format_local(ts, None), "Mon 16 Nov, 08:00 (UTC)")
        self.assertEqual(to_local(ts, "Not/AZone"), ts)  # bad tz falls back to UTC


class StatusTransitionTest(unittest.TestCase):
    def test_transitions(self):
        from paperflux.src.ui.status import transition

        self.assertIsNone(transition(False, False))
        self.assertIsNone(transition(True, True))
        self.assertEqual(transition(True, False), "finished")  # refresh feed + toast
        self.assertEqual(transition(False, True), "started")  # scheduler began while someone watched

    def test_next_update_text_covers_due_and_future(self):
        from types import SimpleNamespace

        from paperflux.src.ui.status import next_update_text

        meta = SimpleNamespace(has_run=True, last_processed_date=datetime(2026, 11, 13, 8, 0, tzinfo=UTC))
        due = next_update_text(meta, datetime(2026, 11, 16, 10, 0, tzinfo=UTC))
        self.assertIn("within the hour", due)
        friday_evening = next_update_text(meta, datetime(2026, 11, 13, 20, 0, tzinfo=UTC))
        self.assertIn("in 2 days", friday_evening)  # Monday, not "tomorrow"
        self.assertIn("Mon 16 Nov", friday_evening)


if __name__ == "__main__":
    unittest.main()
