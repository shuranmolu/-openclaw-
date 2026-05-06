"""
Tests for Reconcile functionality.

Tests conflict detection and supersede relationships.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import init_db
from app.service import MarsService
from app.storage.memory_store import MemoryStore
from app.storage.ledger import RawEventLedger
from app.core.reconciler import MemoryReconciler, auto_reconcile_updates


class TestReconcile(unittest.TestCase):
    """Test cases for memory reconciliation."""

    def setUp(self):
        """Set up test database and service."""
        # Reset the singleton to ensure each test gets a fresh database
        from app.storage.db import Database as DBClass
        DBClass._instance = None
        DBClass._initialized = False

        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.memory_store = MemoryStore(self.db)
        self.service = MarsService(str(self.db_path.resolve()))
        self.reconciler = MemoryReconciler(self.memory_store)

    def tearDown(self):
        """Clean up test database."""
        self.service.db.close()
        shutil.rmtree(self.temp_dir)

    def test_reconcile_active_memories_with_supersede(self):
        """Test reconciling active memories for supersede relationships.

        This test simulates the scenario where:
        1. Two decision memories are created (e.g., Streamlit then Vue+FastAPI)
        2. auto_reconcile_updates should detect the supersede relationship
        3. The older memory should be marked as superseded
        """
        # Create two decision memories with same topic but different content
        # Simulating: first decide Streamlit, then change to Vue + FastAPI

        # Create older memory (10:10 - use Streamlit)
        old_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="一期使用 Streamlit",
            content="一期先用 Streamlit，正式版再考虑 Vue + FastAPI。",
            project_id="carbon_platform",
            status="active",
            valid_time_start="2026-05-01T10:10:00+08:00",
            source_event_ids=None,
        )
        old_memory_id = old_result["memory_id"]

        # Create newer memory (14:10 - change to Vue + FastAPI)
        new_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="改为 Vue + FastAPI",
            content="那就改成 Vue + FastAPI 吧。之前的 Streamlit 方案作废。",
            project_id="carbon_platform",
            status="active",
            valid_time_start="2026-05-01T14:10:00+08:00",
            source_event_ids=None,
        )
        new_memory_id = new_result["memory_id"]

        # Run auto-reconcile on active memories
        results = auto_reconcile_updates("carbon_platform", self.memory_store)

        # Should find and apply the supersede relationship
        self.assertGreater(len(results), 0, "Should find at least one supersede relationship")

        # Verify the results
        self.assertEqual(results[0]["old_memory_id"], old_memory_id)
        self.assertEqual(results[0]["new_memory_id"], new_memory_id)
        self.assertEqual(results[0]["topic"], "技术路线")

        # Check that old memory is marked as superseded
        old_memory = self.memory_store.get_memory(old_memory_id)
        self.assertEqual(old_memory["status"], "superseded")
        self.assertEqual(old_memory["superseded_by"], new_memory_id)

        # Check that new memory is active and has supersedes
        new_memory = self.memory_store.get_memory(new_memory_id)
        self.assertEqual(new_memory["status"], "active")
        self.assertEqual(new_memory["supersedes"], old_memory_id)

    def test_search_current_excludes_superseded(self):
        """Test that searching current memories excludes superseded ones.

        When a memory has been superseded, it should not appear in
        time_scope="current" searches.
        """
        # Create and apply supersede relationship
        old_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="使用 Streamlit",
            content="一期使用 Streamlit。",
            project_id="test_project",
            status="active",
            source_event_ids=None,
        )
        old_memory_id = old_result["memory_id"]

        new_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="改为 Vue + FastAPI",
            content="改成 Vue + FastAPI 吧。",
            project_id="test_project",
            status="active",
            source_event_ids=None,
        )
        new_memory_id = new_result["memory_id"]

        # Apply supersede
        self.reconciler.apply_supersede(old_memory_id, new_memory_id)

        # Search for current memories with time_scope="current"
        current_memories = self.memory_store.search_memories(
            project_id="test_project",
            query="技术路线",
            time_scope="current",
            limit=10,
        )

        # Only the new memory should be in current results
        current_ids = [m["memory_id"] for m in current_memories]
        self.assertIn(new_memory_id, current_ids)
        self.assertNotIn(old_memory_id, current_ids)

    def test_reconcile_new_statement(self):
        """Test reconciling a new statement."""
        # Create an existing memory
        result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="一期使用 Streamlit",
            content="一期 Demo 采用 Streamlit 快速开发。",
            project_id="test_project",
            status="active",
        )
        old_memory_id = result["memory_id"]

        # Reconcile a new statement that indicates change
        reconcile_result = self.reconciler.reconcile_statement(
            project_id="test_project",
            new_statement="改成 Vue + FastAPI 吧",
            auto_resolve=False,
        )

        # Should detect as potential update or supersede
        self.assertIn(reconcile_result["relation"], ["update", "supersede"])
        self.assertEqual(reconcile_result["old_memory_id"], old_memory_id)

    def test_reconcile_unrelated_statement(self):
        """Test reconciling an unrelated statement."""
        # No existing memories
        reconcile_result = self.reconciler.reconcile_statement(
            project_id="test_project",
            new_statement="今天天气不错",
            auto_resolve=False,
        )

        # Should be treated as new
        self.assertEqual(reconcile_result["relation"], "new")
        self.assertIsNone(reconcile_result["old_memory_id"])

    def test_apply_supersede(self):
        """Test applying supersede relationship."""
        # Create old memory
        old_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="使用 Streamlit",
            content="使用 Streamlit 开发。",
            project_id="test_project",
            status="active",
        )
        old_memory_id = old_result["memory_id"]

        # Create new memory
        new_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="使用 Vue + FastAPI",
            content="改成 Vue + FastAPI。",
            project_id="test_project",
            status="pending",
        )
        new_memory_id = new_result["memory_id"]

        # Apply supersede
        success = self.reconciler.apply_supersede(old_memory_id, new_memory_id)
        self.assertTrue(success)

        # Check statuses
        old_memory = self.memory_store.get_memory(old_memory_id)
        new_memory = self.memory_store.get_memory(new_memory_id)

        self.assertEqual(old_memory["status"], "superseded")
        self.assertEqual(new_memory["status"], "active")
        self.assertEqual(old_memory["superseded_by"], new_memory_id)
        self.assertEqual(new_memory["supersedes"], old_memory_id)

    def test_auto_reconcile_updates(self):
        """Test automatic reconciliation."""
        # Create old memory (without evidence to avoid FK issues)
        old_result = self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="使用 Streamlit",
            content="使用 Streamlit。",
            project_id="test_project",
            status="active",
            source_event_ids=None,  # No evidence needed for this test
        )
        old_memory_id = old_result["memory_id"]

        # Create raw event evidence first (required for FK constraint)
        ledger = RawEventLedger(self.db)
        ingest_result = ledger.ingest_messages(
            messages=[{
                "message_id": "msg_001",
                "actor_id": "user_001",
                "content": "改成 Vue + FastAPI",
                "timestamp": "2026-05-01T10:00:00+08:00",
            }],
            project_id="test_project",
        )
        actual_event_id = ingest_result["event_ids"][0]

        # Create a candidate that indicates supersede with actual event ID
        cand_id = self.memory_store.save_candidate(
            candidate_type="decision",
            topic="技术路线",
            summary="改成 Vue + FastAPI",
            project_id="test_project",
            evidence_event_ids=[actual_event_id],  # Use actual event ID
            confidence=0.8,
            need_human_confirm=False,
        )

        # Run auto-reconcile
        results = auto_reconcile_updates("test_project", self.memory_store)

        # Should find and apply the supersede
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["old_memory_id"], old_memory_id)


if __name__ == "__main__":
    unittest.main()
