import asyncio
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from paperflux.src.agents.llm_errors import QuotaExhaustedError
from paperflux.src.agents.paper_analyst.agent import PaperAnalystAgent
from paperflux.src.agents.paper_analyst.brief import split_visual_brief
from paperflux.src.agents.paper_insights.agent import PaperInsightsAgent
from paperflux.src.figures.extract import extract_figures
from paperflux.src.logsetup import bind_paper
from paperflux.src.config.settings import (
    MAX_PAPER_WORKERS,
    MAX_PAPERS,
    PODCASTS_ENABLED,
    load_paper_agent_keys,
    load_podcast_keys,
    load_tts_keys,
)
from paperflux.src.services.database import DatabaseService, paper_is_complete
from paperflux.src.services.paper_fetcher import PaperFetcher
from paperflux.src.services.podcast_producer import PodcastProducer

logger = logging.getLogger("paperflux.paper_processor")

DONE = "done"
FAILED = "failed"
QUOTA = "quota"


class PaperProcessor:
    def __init__(self):
        logger.info("Initializing PaperProcessor")
        self.fetcher = PaperFetcher()
        self.db = DatabaseService()
        self._analyst = None
        self._insights_agent = None
        self._init_runtime_state()

    def _init_runtime_state(self):
        self._running = False
        self._quota_event = threading.Event()
        self._progress_lock = threading.Lock()
        self._done = 0
        self._total = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def _report_progress(self, increment: int = 0, total: int = None):
        with self._progress_lock:
            if total is not None:
                self._total = total
            self._done += increment
            done, total_now = self._done, self._total
        try:
            self.db.set_processing_progress(done, total_now)
        except Exception as e:  # progress is cosmetic, never fail a run over it
            logger.debug("Could not report progress: %s", e)

    @property
    def analyst(self):
        if self._analyst is None:
            self._analyst = PaperAnalystAgent()
            self._warn_if_shared_keys()
        return self._analyst

    @property
    def insights_agent(self):
        if self._insights_agent is None:
            self._insights_agent = PaperInsightsAgent()
            self._warn_if_shared_keys()
        return self._insights_agent

    @property
    def podcast_producer(self):
        """None when podcasts are off or no key is configured for them."""
        if getattr(self, "_podcast_producer", None) is None:
            if not PODCASTS_ENABLED:
                return None
            podcast_keys = load_podcast_keys()
            if not podcast_keys:
                logger.info("Podcasts are off: set GEMINI_PODCAST_API_KEY to a key from its own Google Cloud "
                            "project (they never borrow the analyst/insights quota)")
                return None
            shared = set(podcast_keys + load_tts_keys()) & set(load_paper_agent_keys())
            if shared:
                logger.warning(
                    "Podcasts use %s key(s) that the analyst/insights agents also use, so they spend the same "
                    "project's quota. Create the podcast key in a NEW project to keep them apart.",
                    len(shared),
                )
            self._podcast_producer = PodcastProducer(self.db)
        return self._podcast_producer

    def _run_podcasts(self, paper_ids):
        """After the analyses: one episode per paper, most upvoted first, within today's TTS budget."""
        producer = self.podcast_producer
        if producer is None:
            return

        def progress(done, total):
            try:
                self.db.set_processing_progress(done, total, stage="podcasts")
            except Exception as e:  # cosmetic
                logger.debug("Could not report podcast progress: %s", e)

        try:
            producer.run(paper_ids, on_progress=progress)
        except Exception:  # podcasts are an extra: never fail the papers run over them
            logger.exception("Podcast stage failed; papers are unaffected")

    def _warn_if_shared_keys(self):
        if self._analyst is None or self._insights_agent is None:
            return
        overlap = set(self._analyst.client.api_keys) & set(self._insights_agent.client.api_keys)
        if overlap:
            logger.warning(
                "Analyst and insights agents share %s API key(s). Free-tier limits are per "
                "Google Cloud PROJECT, so keys from the same project share one quota. Use keys "
                "from different projects if you want separate quotas.",
                len(overlap),
            )

    def analyze_and_store_paper(self, paper_entry, pdf_path):
        """Run both agents on one paper and store the result.

        Returns DONE, FAILED, or QUOTA (stop spending requests; retry later).
        Resumable: a stored analysis is reused, and only the missing step is run.
        """
        paper_id = paper_entry["paper"]["id"]
        title = paper_entry["paper"].get("title", "")
        summary = paper_entry["paper"].get("summary", "")

        with bind_paper(paper_id):
            return self._analyze_and_store(paper_entry, pdf_path, paper_id, title, summary)

    def _analyze_and_store(self, paper_entry, pdf_path, paper_id, title, summary):
        started = time.perf_counter()
        try:
            existing = self.db.collection.find_one({"paper_id": paper_id})
            if paper_is_complete(existing):
                logger.info("Paper %s already complete, skipping", paper_id)
                return DONE
            if self._quota_event.is_set():
                return QUOTA

            explanation = (existing or {}).get("explanation")
            visual_brief = (existing or {}).get("visual_brief") or ""
            if not explanation:
                logger.info("Analyst starting")
                stage = time.perf_counter()
                explanation = self.analyst.run(
                    pdf_path=pdf_path, title=title, summary=summary, paper_id=paper_id
                )
                logger.info("Analyst finished in %.1fs (%s chars)", time.perf_counter() - stage, len(explanation or ""))
            explanation, fresh_brief = split_visual_brief(explanation)
            visual_brief = fresh_brief or visual_brief

            insights = {"insights": []}
            quota_hit = False
            try:
                logger.info("Insights starting")
                stage = time.perf_counter()
                insights = self.insights_agent.run(
                    pdf_path=pdf_path,
                    explanation=explanation,
                    title=title,
                    paper_id=paper_id,
                    abstract=summary,
                    visual_brief=visual_brief,
                )
                logger.info("Insights finished in %.1fs", time.perf_counter() - stage)
            except QuotaExhaustedError as e:
                quota_hit = True
                self._quota_event.set()
                logger.warning("Insights quota exhausted after %.1fs: %s", time.perf_counter() - stage, e)
                insights = {"insights": [], "raw": "", "parse_error": "Daily free-tier quota reached; will retry."}
            except Exception as e:
                logger.exception("Insights failed after %.1fs", time.perf_counter() - stage)
                insights = {"insights": [], "raw": "", "parse_error": str(e)}

            paper_obj = self.fetcher.parse_paper_data(paper_entry)
            paper_obj.explanation = explanation
            paper_obj.visual_brief = visual_brief
            paper_obj.insights = insights
            logger.info("Storing paper")
            self.db.upsert_paper(paper_obj)
            self._store_figures(paper_id, pdf_path)
            logger.info("Paper %s in %.1fs", "waiting on quota" if quota_hit else "stored",
                        time.perf_counter() - started)
            return QUOTA if quota_hit else DONE

        except QuotaExhaustedError as e:
            self._quota_event.set()
            logger.warning("Analyst quota exhausted after %.1fs: %s", time.perf_counter() - started, e)
            return QUOTA
        except Exception:
            logger.exception("Paper failed after %.1fs", time.perf_counter() - started)
            return FAILED

        finally:
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except Exception as e:
                    logger.warning("Could not remove temporary file %s: %s", pdf_path, e)

    async def process_papers(self):
        """Process today's top papers. Never wipes existing data until new data exists."""
        if self._running:
            logger.warning("Previous processing still running, skipping...")
            return False

        self._running = True
        self._quota_event.clear()
        self.db.set_processing_status(True)
        logger.info("Starting paper processing")
        started = time.perf_counter()
        completed = False

        try:
            papers = await self.fetcher.fetch_papers()
            papers = self.fetcher.select_top(papers, MAX_PAPERS)
            today_ids = [p["paper"]["id"] for p in papers]
            logger.info("Selected top %s papers by upvotes", len(papers))

            todo = [p for p in papers if not self.db.has_complete_paper(p["paper"]["id"])]
            self._done = 0
            self._report_progress(increment=len(papers) - len(todo), total=len(papers))
            paper_paths = await self.fetcher.download_papers(todo)
            logger.info("Downloaded %s/%s PDFs for %s pending papers", len(paper_paths), len(todo), len(todo))

            max_workers = max(1, min(MAX_PAPER_WORKERS, 4))
            results = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for paper in todo:
                    pid = paper["paper"]["id"]
                    if pid in paper_paths:
                        futures[pid] = executor.submit(self.analyze_and_store_paper, paper, paper_paths[pid])
                    else:
                        logger.warning("Skipping paper %s - PDF download failed", pid)
                        results[pid] = FAILED
                for pid, future in futures.items():
                    results[pid] = future.result()
                    if results[pid] == DONE:
                        self._report_progress(increment=1)

            await self._backfill_figures(papers)

            done = sum(1 for r in results.values() if r == DONE)
            quota = sum(1 for r in results.values() if r == QUOTA)
            logger.info("Papers finished in %.0fs: %s done, %s waiting on quota, %s failed",
                        time.perf_counter() - started, done, quota,
                        sum(1 for r in results.values() if r == FAILED))
            await asyncio.to_thread(self._run_podcasts, today_ids)

            # Prune yesterday's papers only once today's batch has something to show.
            stored_today = [pid for pid in today_ids if self.db.get_paper_by_id(pid)]
            if stored_today:
                removed = self.db.delete_papers_not_in(today_ids)
                logger.info("Pruned %s papers from previous days", removed)

            # While quota is the only blocker, keep the day open so the hourly scheduler
            # tick finishes the rest after the quota resets. Otherwise close the day.
            if quota == 0:
                self.db.update_last_processed_date()
            completed = True
            return True

        except Exception:
            logger.exception("Paper processing failed after %.0fs", time.perf_counter() - started)
            return False

        finally:
            self._running = False
            self.db.set_processing_status(False)
            if not completed:
                logger.info("Run did not complete; existing papers were left untouched")

    def _needs_figures(self, paper_id: str) -> bool:
        """A stored paper we have not tried to lift figures from yet."""
        doc = self.db.get_paper_by_id(paper_id)
        return bool(doc) and "figures" not in (doc or {})

    def _store_figures(self, paper_id: str, pdf_path: str) -> None:
        """Lift figures from the PDF already on disk. Never raises into the paper run."""
        if not pdf_path:
            return
        try:
            found = extract_figures(pdf_path)
            if found is None:
                logger.info("Figures for %s could not be read; will retry next run", paper_id)
                return
            self.db.save_paper_figures(paper_id, found)
            logger.info("Stored %s figure(s) for %s", len(found), paper_id)
        except Exception:
            logger.exception("Could not store figures")

    async def _backfill_figures(self, papers):
        """Papers already analyzed before figure lifting existed still get their figures.

        This downloads the PDF only, and never calls a model.
        """
        pending = [p for p in papers if self._needs_figures(p["paper"]["id"])]
        if not pending:
            return
        paths = await self.fetcher.download_papers(pending)
        for pid, path in paths.items():
            try:
                with bind_paper(pid):
                    self._store_figures(pid, path)
            finally:
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as exc:
                        logger.warning("Could not remove temporary file %s: %s", path, exc)
