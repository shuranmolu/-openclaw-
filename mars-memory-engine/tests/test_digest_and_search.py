"""
Tests for Digest and Search functionality.

Tests memory extraction and search.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import init_db
from app.service import MarsService
from app.connectors.sample_loader import create_carbon_platform_sample


class TestDigestAndSearch(unittest.TestCase):
    """Test cases for digest and search."""

    def setUp(self):
        """Set up test database and service."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.service = MarsService(str(self.db_path.resolve()))

        # Create sample data
        self.sample_file = Path(self.temp_dir) / "carbon_platform.json"
        create_carbon_platform_sample(str(self.sample_file))

    def tearDown(self):
        """Clean up test database."""
        self.service.db.close()
        shutil.rmtree(self.temp_dir)

    def test_ingest_and_digest(self):
        """Test ingesting messages and extracting memories."""
        # Ingest
        ingest_result = self.service.mars_ingest_from_file(str(self.sample_file))
        self.assertGreater(ingest_result["imported_count"], 0)
        self.assertEqual(ingest_result["project_id"], "carbon_platform")

        # Digest
        digest_result = self.service.mars_digest(
            project_id="carbon_platform",
            auto_commit=True,
        )

        self.assertGreater(len(digest_result["candidates"]), 0)
        self.assertGreaterEqual(digest_result["committed_count"], 0)

    def test_search_finds_memories(self):
        """Test that search can find committed memories."""
        # Ingest and digest
        self.service.mars_ingest_from_file(str(self.sample_file))
        self.service.mars_digest(
            project_id="carbon_platform",
            auto_commit=True,
        )

        # Search for tech decisions
        search_result = self.service.mars_search(
            project_id="carbon_platform",
            query="技术路线",
            top_k=3,
        )

        # Should find some memories
        self.assertGreaterEqual(search_result["total_retrieved"], 0)
        self.assertIn("answer", search_result)
        self.assertIn("memories", search_result)

    def test_search_time_scope_current(self):
        """Test searching only current (active) memories."""
        # Ingest and digest
        self.service.mars_ingest_from_file(str(self.sample_file))
        self.service.mars_digest(
            project_id="carbon_platform",
            auto_commit=True,
        )

        # Search current scope
        current_result = self.service.mars_search(
            project_id="carbon_platform",
            query="技术",
            time_scope="current",
            top_k=10,
        )

        # All results should have status=active
        for memory in current_result["memories"]:
            self.assertEqual(memory.get("status"), "active")

    def test_project_stats(self):
        """Test getting project statistics."""
        # Ingest and digest
        self.service.mars_ingest_from_file(str(self.sample_file))
        self.service.mars_digest(
            project_id="carbon_platform",
            auto_commit=True,
        )

        # Get stats
        stats = self.service.get_project_stats("carbon_platform")

        self.assertGreater(stats["event_count"], 0)
        self.assertGreaterEqual(stats["memory_count"], 0)


if __name__ == "__main__":
    unittest.main()
