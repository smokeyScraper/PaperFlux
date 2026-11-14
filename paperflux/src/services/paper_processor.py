import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from paperflux.src.agents.llm_errors import QuotaExhaustedError
from paperflux.src.agents.paper_analyst.agent import PaperAnalystAgent
from paperflux.src.agents.paper_insights.agent import PaperInsightsAgent
from paperflux.src.config.settings import MAX_PAPER_WORKERS, MAX_PAPERS
from paperflux.src.services.database import DatabaseService, paper_is_complete
from paperflux.src.services.paper_fetcher import PaperFetcher

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
        self._running = False
        self._quota_event = threading.Event()

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

        try:
            existing = self.db.collection.find_one({"paper_id": paper_id})
            if paper_is_complete(existing):
                logger.info("Paper %s already complete, skipping", paper_id)
                return DONE
            if self._quota_event.is_set():
                return QUOTA

            explanation = (existing or {}).get("explanation")
            if not explanation:
                logger.info("Analyst agent starting for %s", paper_id)
                explanation = self.analyst.run(
                    pdf_path=pdf_path, title=title, summary=summary, paper_id=paper_id
                )

            insights = {"insights": []}
            quota_hit = False
            try:
                logger.info("Insights agent starting for %s", paper_id)
                insights = self.insights_agent.run(
                    pdf_path=pdf_path,
                    explanation=explanation,
                    title=title,
                    paper_id=paper_id,
                    abstract=summary,
                )
            except QuotaExhaustedError as e:
                quota_hit = True
                self._quota_event.set()
                logger.warning("Insights quota exhausted for %s: %s", paper_id, e)
                insights = {"insights": [], "raw": "", "parse_error": "Daily free-tier quota reached; will retry."}
            except Exception as e:
                logger.error("Insights agent failed for %s: %s", paper_id, e)
                insights = {"insights": [], "raw": "", "parse_error": str(e)}

            paper_obj = self.fetcher.parse_paper_data(paper_entry)
            paper_obj.explanation = explanation
            paper_obj.insights = insights
            logger.info("Storing paper %s", paper_id)
            self.db.upsert_paper(paper_obj)
            return QUOTA if quota_hit else DONE

        except QuotaExhaustedError as e:
            self._quota_event.set()
            logger.warning("Analyst quota exhausted at %s: %s", paper_id, e)
            return QUOTA
        except Exception as e:
            logger.error("Error processing paper %s: %s", paper_id, e)
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
        logger.info("Starting paper processing...")
        completed = False

        try:
            papers = await self.fetcher.fetch_papers()
            papers = self.fetcher.select_top(papers, MAX_PAPERS)
            today_ids = [p["paper"]["id"] for p in papers]
            logger.info("Selected top %s papers by upvotes", len(papers))

            todo = [p for p in papers if not self.db.has_complete_paper(p["paper"]["id"])]
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

            done = sum(1 for r in results.values() if r == DONE)
            quota = sum(1 for r in results.values() if r == QUOTA)
            logger.info("Processed: %s done, %s waiting on quota, %s failed", done, quota,
                        sum(1 for r in results.values() if r == FAILED))

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

        except Exception as e:
            logger.error("Error in paper processing: %s", e)
            return False

        finally:
            self._running = False
            self.db.set_processing_status(False)
            if not completed:
                logger.info("Run did not complete; existing papers were left untouched")
