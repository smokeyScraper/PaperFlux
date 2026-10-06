import os
import logging
from concurrent.futures import ThreadPoolExecutor

from paperflux.src.agents.paper_analyst.agent import PaperAnalystAgent
from paperflux.src.agents.paper_insights.agent import PaperInsightsAgent
from paperflux.src.config.settings import MAX_PAPER_WORKERS
from paperflux.src.services.paper_fetcher import PaperFetcher
from paperflux.src.services.database import DatabaseService

logger = logging.getLogger("paperflux.paper_processor")


class PaperProcessor:
    def __init__(self):
        logger.info("Initializing PaperProcessor")
        self.fetcher = PaperFetcher()
        self.db = DatabaseService()
        self._analyst = None
        self._insights_agent = None
        self._running = False

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
        analyst_keys = set(self._analyst.client.api_keys)
        insights_keys = set(self._insights_agent.client.api_keys)
        overlap = analyst_keys & insights_keys
        if overlap:
            logger.warning(
                "Analyst and insights agents share %s API key(s). "
                "Set GEMINI_ANALYST_API_KEY and GEMINI_INSIGHTS_API_KEY to "
                "separate keys so each agent keeps its own Flash quota.",
                len(overlap),
            )

    def analyze_and_store_paper(self, paper_entry, pdf_path):
        """Run both agents on one paper and store the result."""
        paper_id = paper_entry["paper"]["id"]
        title = paper_entry["paper"].get("title", "")
        summary = paper_entry["paper"].get("summary", "")

        try:
            logger.info("Analyst agent starting for %s", paper_id)
            explanation = self.analyst.run(
                pdf_path=pdf_path,
                title=title,
                summary=summary,
                paper_id=paper_id,
            )

            insights = {"insights": []}
            try:
                logger.info("Insights agent starting for %s", paper_id)
                insights = self.insights_agent.run(
                    pdf_path=pdf_path,
                    explanation=explanation,
                    title=title,
                    paper_id=paper_id,
                )
            except Exception as e:
                logger.error("Insights agent failed for %s: %s", paper_id, e)
                insights = {
                    "insights": [],
                    "raw": "",
                    "parse_error": str(e),
                }

            paper_obj = self.fetcher.parse_paper_data(paper_entry)
            paper_obj.explanation = explanation
            paper_obj.insights = insights

            logger.info("Storing paper %s", paper_id)
            self.db.insert_paper(paper_obj)
            return True

        except Exception as e:
            logger.error("Error processing paper %s: %s", paper_id, e)
            return False

        finally:
            if pdf_path and os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    logger.debug("Removed temporary file: %s", pdf_path)
                except Exception as e:
                    logger.warning("Could not remove temporary file %s: %s", pdf_path, e)

    async def process_papers(self):
        """Process all daily papers"""
        if self._running:
            logger.warning("Previous processing still running, skipping...")
            return False

        self._running = True
        self.db.set_processing_status(True)
        logger.info("Starting paper processing...")

        try:
            self.db.clear_papers_collection()

            papers = await self.fetcher.fetch_papers()
            logger.info("Fetched %s papers, downloading PDFs...", len(papers))

            paper_paths = await self.fetcher.download_papers(papers)
            logger.info(
                "Successfully downloaded %s out of %s papers",
                len(paper_paths),
                len(papers),
            )

            max_workers = max(1, min(MAX_PAPER_WORKERS, 4))
            logger.info("Starting agent pipeline with %s workers", max_workers)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for paper in papers:
                    paper_id = paper["paper"]["id"]
                    if paper_id in paper_paths:
                        futures.append(
                            executor.submit(
                                self.analyze_and_store_paper,
                                paper,
                                paper_paths[paper_id],
                            )
                        )
                    else:
                        logger.warning("Skipping paper %s - PDF download failed", paper_id)

                processed_count = sum(1 for future in futures if future.result())
                logger.info(
                    "Successfully processed %s out of %s papers",
                    processed_count,
                    len(futures),
                )

            self.db.update_last_processed_date()
            logger.info("Paper processing completed successfully")
            return True

        except Exception as e:
            logger.error("Error in paper processing: %s", e)
            return False

        finally:
            self._running = False
            self.db.set_processing_status(False)
