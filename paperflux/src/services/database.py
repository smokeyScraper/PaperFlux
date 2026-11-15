import time
from datetime import datetime, timezone

import gridfs
from gridfs.errors import NoFile
from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from paperflux.src.models.models import Paper, ProcessingMetadata
from paperflux.src.services.schedule_rules import is_stale, quota_day
import threading
import logging
import os
from paperflux.src.config.settings import DB_NAME, COLLECTION_NAME, METADATA_COLLECTION
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("paperflux.database")
PODCAST_BUCKET = "podcast_audio"
FIGURE_BUCKET = "paper_figures"


def paper_is_complete(doc) -> bool:
    """A stored paper needs no more model calls when it has an analysis and a usable v3 pack."""
    if not doc or not doc.get("explanation"):
        return False
    insights = doc.get("insights") or {}
    return insights.get("schema_version") == 3 and bool(insights.get("insights"))


class DatabaseService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabaseService, cls).__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        logger.info("Initializing DatabaseService")
        self.client = MongoClient(os.environ["MONGODB_URI"])
        self.db = self.client[DB_NAME]
        self.collection = self.db[COLLECTION_NAME]
        self.metadata_collection = self.db[METADATA_COLLECTION]
        self._cache = {}
        self._cache_timestamp = 0
        self._cache_lock = threading.Lock()
        self._initialized = True

    def clear_papers_collection(self):
        """Clear the papers collection"""
        logger.info("Clearing papers collection")
        self.collection.delete_many({})
        with self._cache_lock:
            self._cache = {}
            self._cache_timestamp = 0

    def insert_paper(self, paper: Paper):
        """Insert a paper into the database"""
        logger.info(f"Inserting paper: {paper.paper_id}")
        result = self.collection.insert_one(paper.to_dict())
        # Invalidate cache
        with self._cache_lock:
            self._cache = {}
            self._cache_timestamp = 0
        return result

    def _invalidate_cache(self):
        with self._cache_lock:
            self._cache = {}
            self._cache_timestamp = 0

    def upsert_paper(self, paper: Paper):
        """Insert or update one paper by id. ``$set`` (not replace) so fields written by later
        pipeline stages, like ``podcast``, survive a re-run of an earlier stage."""
        logger.info(f"Upserting paper: {paper.paper_id}")
        doc = paper.to_dict()
        result = self.collection.update_one({"paper_id": paper.paper_id}, {"$set": doc}, upsert=True)
        self._invalidate_cache()
        return result

    def delete_papers_not_in(self, keep_ids):
        """Remove papers that are not part of the current batch (and their podcast audio)."""
        keep = list(keep_ids)
        result = self.collection.delete_many({"paper_id": {"$nin": keep}})
        self._prune_bucket(self.audio_bucket, keep)
        self._prune_bucket(self.figure_bucket, keep)
        self._invalidate_cache()
        return result.deleted_count

    def _prune_bucket(self, bucket, keep):
        try:
            for stale in bucket.find({"metadata.paper_id": {"$nin": keep}}):
                bucket.delete(stale._id)
        except Exception:  # file cleanup must never block pruning papers
            logger.warning("Could not prune stored files", exc_info=True)

    # ---- podcasts ------------------------------------------------------------
    @property
    def audio_bucket(self):
        """GridFS bucket for episode audio: files are too big to live in the paper documents."""
        if getattr(self, "_audio_bucket", None) is None:
            self._audio_bucket = gridfs.GridFSBucket(self.db, bucket_name=PODCAST_BUCKET)
        return self._audio_bucket

    def set_podcast(self, paper_id: str, podcast: dict):
        self.collection.update_one({"paper_id": paper_id}, {"$set": {"podcast": podcast}})
        self._invalidate_cache()

    def save_podcast_audio(self, paper_id: str, data: bytes, mime: str) -> str:
        """Store one episode, replacing any earlier file for the paper. Returns the file id."""
        for old in self.audio_bucket.find({"metadata.paper_id": paper_id}):
            self.audio_bucket.delete(old._id)
        ext = "mp3" if mime == "audio/mpeg" else "wav"
        file_id = self.audio_bucket.upload_from_stream(
            f"{paper_id}.{ext}", data, metadata={"paper_id": paper_id, "mime": mime}
        )
        return str(file_id)

    def get_podcast_audio(self, file_id: str):
        return self._read_file(self.audio_bucket, file_id)

    # ---- figures from the paper ---------------------------------------------
    @property
    def figure_bucket(self):
        """GridFS bucket for figures lifted from the PDF. Bytes never go in the paper document."""
        if getattr(self, "_figure_bucket", None) is None:
            self._figure_bucket = gridfs.GridFSBucket(self.db, bucket_name=FIGURE_BUCKET)
        return self._figure_bucket

    def save_paper_figures(self, paper_id: str, figures) -> None:
        """Replace this paper's figures. ``figures`` is a list of PaperFigure (png bytes included)."""
        for old in self.figure_bucket.find({"metadata.paper_id": paper_id}):
            self.figure_bucket.delete(old._id)
        stored = []
        for fig in figures:
            file_id = self.figure_bucket.upload_from_stream(
                f"{paper_id}-figure-{fig.number}.png",
                fig.png,
                metadata={"paper_id": paper_id, "mime": "image/png"},
            )
            stored.append({
                "label": fig.label,
                "caption": fig.caption,
                "page": fig.page,
                "file_id": str(file_id),
            })
        self.collection.update_one({"paper_id": paper_id}, {"$set": {"figures": stored}})
        self._invalidate_cache()

    def get_paper_figure(self, file_id: str):
        return self._read_file(self.figure_bucket, file_id)

    @staticmethod
    def _read_file(bucket, file_id: str):
        try:
            return bucket.open_download_stream(ObjectId(file_id)).read()
        except (NoFile, InvalidId):
            return None

    def reserve_tts_request(self, limit: int) -> bool:
        """Atomically claim one of today's TTS requests (quota day = Pacific date)."""
        try:
            self.metadata_collection.find_one_and_update(
                {"_id": f"tts_usage_{quota_day()}", "count": {"$lt": int(limit)}},
                {"$inc": {"count": 1}},
                upsert=True,
            )
            return True
        except DuplicateKeyError:  # today's document exists and is already at the limit
            return False

    def tts_requests_today(self) -> int:
        doc = self.metadata_collection.find_one({"_id": f"tts_usage_{quota_day()}"}) or {}
        return int(doc.get("count", 0) or 0)

    def has_complete_paper(self, paper_id: str) -> bool:
        """True if the paper is already stored with an analysis AND a v3 insights pack."""
        doc = self.collection.find_one({"paper_id": paper_id})
        return paper_is_complete(doc)

    def get_all_papers(self, max_cache_age_seconds=20):
        """Get all papers, with caching for better performance"""
        current_time = time.time()

        # Check cache validity
        with self._cache_lock:
            if (
                self._cache
                and current_time - self._cache_timestamp <= max_cache_age_seconds
            ):
                return self._cache.get("all_papers", [])

        # Cache miss
        logger.debug("Cache miss for all_papers, fetching from database")
        papers = list(self.collection.find())

        # Update cache
        with self._cache_lock:
            self._cache["all_papers"] = papers
            self._cache_timestamp = current_time

        return papers

    def get_paper_by_id(self, paper_id: str):
        """Get a paper by ID with caching"""
        with self._cache_lock:
            if "all_papers" in self._cache:
                for paper in self._cache["all_papers"]:
                    if paper["paper_id"] == paper_id:
                        return paper
        
        # Cache miss
        return self.collection.find_one({"paper_id": paper_id})

    def get_papers_count(self):
        """Get the count of papers in the database"""
        return self.collection.count_documents({})

    def update_last_processed_date(self):
        """Update the last processed date to now"""
        now = datetime.now(timezone.utc)
        logger.info(f"Updating last processed date to {now}")
        
        # Update or insert the processing metadata
        self.metadata_collection.update_one(
            {"_id": "processing_metadata"},
            {"$set": {"last_processed_date": now, "is_processing": False}},
            upsert=True
        )

    def set_processing_status(self, is_processing: bool):
        """Set the processing flag. A start timestamp lets us heal a flag left behind
        by a run that died (e.g. the Space restarted mid-processing)."""
        logger.info(f"Setting processing status to {is_processing}")
        update = {"is_processing": is_processing}
        if is_processing:
            update.update({"processing_started_at": datetime.now(timezone.utc),
                           "progress_done": 0, "progress_total": 0, "stage": "papers"})
        self.metadata_collection.update_one(
            {"_id": "processing_metadata"}, {"$set": update}, upsert=True
        )

    def set_processing_progress(self, done: int, total: int, stage: str = "papers"):
        """Live progress for the UI progress bar (``stage`` is "papers" or "podcasts")."""
        self.metadata_collection.update_one(
            {"_id": "processing_metadata"},
            {"$set": {"progress_done": int(done), "progress_total": int(total), "stage": stage}},
            upsert=True,
        )

    def get_processing_metadata(self) -> ProcessingMetadata:
        """Get the processing metadata (self-healing a stale 'processing' flag)"""
        data = self.metadata_collection.find_one({"_id": "processing_metadata"})

        if not data:
            # No metadata exists yet
            return ProcessingMetadata()

        metadata = ProcessingMetadata()
        metadata.has_run = "last_processed_date" in data
        metadata.last_processed_date = data.get("last_processed_date", datetime.now(timezone.utc))
        metadata.is_processing = data.get("is_processing", False)
        metadata.progress_done = int(data.get("progress_done", 0) or 0)
        metadata.progress_total = int(data.get("progress_total", 0) or 0)
        metadata.stage = data.get("stage") or "papers"

        if metadata.is_processing and is_stale(
            data.get("processing_started_at"), datetime.now(timezone.utc)
        ) and data.get("processing_started_at") is not None:
            logger.warning("Clearing a stale processing flag left by a run that never finished")
            self.metadata_collection.update_one(
                {"_id": "processing_metadata"}, {"$set": {"is_processing": False}}
            )
            metadata.is_processing = False

        return metadata

    def should_process_today(self) -> bool:
        """Check if papers should be processed today based on last processed date"""
        metadata = self.get_processing_metadata()
        
        # If already processing, don't start another process
        if metadata.is_processing:
            logger.info("Paper processing is already running")
            return False
            
        # Get the date (not time) of the last processing
        last_date = metadata.last_processed_date.date()
        today = datetime.now(timezone.utc).date()
        
        # If we haven't processed today, or if we have no papers, we should process
        papers_count = self.get_papers_count()
        should_process = (last_date < today) or (papers_count == 0)
        
        # Manual trigger by user, need a fix
        if should_process:
            logger.info(f"Should process papers. Last processed: {last_date}, Today: {today}, Papers count: {papers_count}")
        else:
            logger.info(f"No need to process papers. Last processed: {last_date}, Today: {today}")
            
        return should_process