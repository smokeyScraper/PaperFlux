import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from paperflux.src.services import database


def make_db(doc):
    database.DatabaseService._instance = None
    with patch.dict(os.environ, {"MONGODB_URI": "mongodb://stub"}), patch.object(database, "MongoClient", return_value=MagicMock()):
        db = database.DatabaseService()
    db.metadata_collection = MagicMock()
    db.metadata_collection.find_one.return_value = doc
    return db


class ProcessingStateTest(unittest.TestCase):
    def tearDown(self):
        database.DatabaseService._instance = None

    def test_fresh_database_reports_never_run(self):
        meta = make_db(None).get_processing_metadata()
        self.assertFalse(meta.has_run)
        self.assertFalse(meta.is_processing)

    def test_stale_processing_flag_is_healed(self):
        started = datetime.now(timezone.utc) - timedelta(hours=5)
        db = make_db({"is_processing": True, "processing_started_at": started, "last_processed_date": started})
        meta = db.get_processing_metadata()
        self.assertFalse(meta.is_processing)  # the UI must not say "processing" forever
        update = db.metadata_collection.update_one.call_args.args[1]
        self.assertEqual(update, {"$set": {"is_processing": False}})

    def test_recent_processing_flag_is_kept_and_progress_is_read(self):
        started = datetime.now(timezone.utc) - timedelta(minutes=10)
        db = make_db({"is_processing": True, "processing_started_at": started, "progress_done": 3, "progress_total": 10})
        meta = db.get_processing_metadata()
        self.assertTrue(meta.is_processing)
        self.assertEqual((meta.progress_done, meta.progress_total), (3, 10))
        db.metadata_collection.update_one.assert_not_called()

    def test_legacy_flag_without_timestamp_is_not_guessed_stale(self):
        db = make_db({"is_processing": True, "last_processed_date": datetime.now(timezone.utc)})
        self.assertTrue(db.get_processing_metadata().is_processing)

    def test_starting_a_run_records_timestamp_and_resets_progress(self):
        db = make_db(None)
        db.set_processing_status(True)
        update = db.metadata_collection.update_one.call_args.args[1]["$set"]
        self.assertTrue(update["is_processing"])
        self.assertIn("processing_started_at", update)
        self.assertEqual((update["progress_done"], update["progress_total"]), (0, 0))
        db.set_processing_status(False)
        self.assertEqual(db.metadata_collection.update_one.call_args.args[1]["$set"], {"is_processing": False})


if __name__ == "__main__":
    unittest.main()
