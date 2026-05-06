"""
Tests for Raw Event Ledger.

Tests idempotency and message ingestion.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import Database
from app.storage.ledger import RawEventLedger


class TestRawEventLedger(unittest.TestCase):
    """Test cases for RawEventLedger."""

    def setUp(self):
        """Set up test database."""
        # Reset the singleton to ensure each test gets a fresh database
        from app.storage.db import Database as DBClass
        DBClass._instance = None
        DBClass._initialized = False

        # Use unique temp dir for each test to avoid conflicts
        import time
        unique_id = str(int(time.time() * 1000000))
        self.temp_dir = tempfile.mkdtemp(prefix=f"test_mars_{unique_id}_")
        self.db_path = Path(self.temp_dir) / "test.db"
        # Create a fresh Database instance for this test
        self.db = Database(str(self.db_path.resolve()))
        self.db.initialize_schema()
        self.ledger = RawEventLedger(self.db)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        # Reset singleton for next test
        from app.storage.db import Database as DBClass
        DBClass._instance = None
        DBClass._initialized = False
        # Close all connections and remove temp dir
        try:
            shutil.rmtree(self.temp_dir)
        except:
            pass

    def test_ingest_messages(self):
        """Test basic message ingestion."""
        messages = [
            {
                "message_id": "msg_001",
                "actor_id": "user_001",
                "content": "Hello world",
                "timestamp": "2026-05-01T10:00:00+08:00",
            },
            {
                "message_id": "msg_002",
                "actor_id": "user_002",
                "content": "Test message",
                "timestamp": "2026-05-01T10:01:00+08:00",
            },
        ]

        result = self.ledger.ingest_messages(
            messages=messages,
            project_id="test_project",
        )

        self.assertEqual(result["imported_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(len(result["event_ids"]), 2)

    def test_idempotency(self):
        """Test that duplicate messages are skipped."""
        messages = [
            {
                "message_id": "msg_001",
                "actor_id": "user_001",
                "content": "Hello world",
                "timestamp": "2026-05-01T10:00:00+08:00",
            },
        ]

        # First import
        result1 = self.ledger.ingest_messages(
            messages=messages,
            project_id="test_project",
        )
        self.assertEqual(result1["imported_count"], 1)
        self.assertEqual(result1["skipped_count"], 0)

        # Second import (should skip)
        result2 = self.ledger.ingest_messages(
            messages=messages,
            project_id="test_project",
        )
        self.assertEqual(result2["imported_count"], 0)
        self.assertEqual(result2["skipped_count"], 1)

    def test_get_events_by_project(self):
        """Test retrieving events by project."""
        messages = [
            {
                "message_id": "msg_001",
                "actor_id": "user_001",
                "content": "Hello",
                "timestamp": "2026-05-01T10:00:00+08:00",
            },
            {
                "message_id": "msg_002",
                "actor_id": "user_002",
                "content": "World",
                "timestamp": "2026-05-01T10:01:00+08:00",
            },
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")

        events = self.ledger.get_events_by_project("test_project")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["content"], "Hello")
        self.assertEqual(events[1]["content"], "World")

    def test_get_event_count(self):
        """Test counting events."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "user_001",
                "content": f"Message {i}",
                "timestamp": "2026-05-01T10:00:00+08:00",
            }
            for i in range(5)
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")

        count = self.ledger.get_event_count("test_project")
        self.assertEqual(count, 5)


if __name__ == "__main__":
    unittest.main()
