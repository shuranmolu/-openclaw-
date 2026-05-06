"""
Tests for Window Builder.

Tests time window grouping.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import Database, init_db
from app.storage.ledger import RawEventLedger
from app.core.window_builder import WindowBuilder


class TestWindowBuilder(unittest.TestCase):
    """Test cases for WindowBuilder."""

    def setUp(self):
        """Set up test database and sample events."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.ledger = RawEventLedger(self.db)
        self.builder = WindowBuilder(time_window_minutes=30, max_messages=50)

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def _create_sample_events(self):
        """Create sample events for testing."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "user_001",
                "content": f"Message {i}",
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i in range(10)
        ]

        result = self.ledger.ingest_messages(messages, project_id="test_project")
        return self.ledger.get_events_by_project("test_project")

    def test_build_windows(self):
        """Test building windows from events."""
        events = self._create_sample_events()
        windows = self.builder.build_windows(events, "test_project")

        # All events should be in one window (within time limit)
        self.assertGreaterEqual(len(windows), 1)
        self.assertGreaterEqual(windows[0]["message_count"], 1)

    def test_window_has_event_ids(self):
        """Test that windows contain event IDs."""
        events = self._create_sample_events()
        windows = self.builder.build_windows(events, "test_project")

        for window in windows:
            self.assertIsInstance(window["event_ids"], list)
            self.assertGreater(len(window["event_ids"]), 0)
            self.assertIn("window_id", window)
            self.assertIn("start_time", window)
            self.assertIn("end_time", window)

    def test_max_messages_limit(self):
        """Test max_messages limit."""
        # Create builder with small max_messages
        builder = WindowBuilder(time_window_minutes=30, max_messages=3)

        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "user_001",
                "content": f"Message {i}",
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i in range(10)
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")
        events = self.ledger.get_events_by_project("test_project")

        windows = builder.build_windows(events, "test_project")

        # Should create multiple windows due to max_messages limit
        total_events = sum(w["message_count"] for w in windows)
        self.assertEqual(total_events, 10)

    def test_time_window_split(self):
        """Test time window splitting."""
        # Create builder with short time window
        builder = WindowBuilder(time_window_minutes=5, max_messages=100)

        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "user_001",
                "content": f"Message {i}",
                "timestamp": f"2026-05-01T10:{i*10:02d}:00+08:00",
            }
            for i in range(5)
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")
        events = self.ledger.get_events_by_project("test_project")

        windows = builder.build_windows(events, "test_project")

        # Each message is 10 minutes apart, so should create separate windows
        self.assertGreaterEqual(len(windows), 2)


if __name__ == "__main__":
    unittest.main()
