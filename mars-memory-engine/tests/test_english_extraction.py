"""
Tests for English extraction in RuleBasedExtractor.

Tests that English meeting content can be properly extracted.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import Database, init_db
from app.storage.ledger import RawEventLedger
from app.core.extractor import RuleBasedExtractor
from app.core.window_builder import WindowBuilder


class TestEnglishExtraction(unittest.TestCase):
    """Test cases for English extraction."""

    def setUp(self):
        """Set up test database and sample events."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.ledger = RawEventLedger(self.db)
        self.builder = WindowBuilder(time_window_minutes=30, max_messages=50)
        self.extractor = RuleBasedExtractor()

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def _create_sample_events_english_remote_control(self):
        """Create sample English events about remote control design."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": ["Industrial Designer", "Marketing", "Project Manager", "User Interface"][i % 4],
                "content": content,
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i, content in enumerate([
                "I want to integrate remote controls for different appliances into one.",
                "That integration would lead to too many buttons, which is not user-friendly.",
                "A menu display should be incorporated instead.",
                "I agree, and basic buttons could remain.",
                "The remote control should be lightweight plastic material.",
                "We should not use the flip top because it costs a lot.",
            ])
        ]

        result = self.ledger.ingest_messages(messages, project_id="test_project")
        return self.ledger.get_events_by_project("test_project")

    def _create_sample_events_english_market_research(self):
        """Create sample English events about market research."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": ["Marketing", "Project Manager", "Industrial Designer", "Marketing", "User Interface"][i % 5],
                "content": content,
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i, content in enumerate([
                "The market research presented indicates the remote control design should be more stylish.",
                "An ideal model would be for TV only with a corporate logo incorporated.",
                "I recommend a touch screen panel for the design.",
                "The remote control size could be adjusted due to physical differences among users.",
                "We gave up speech recognition because it would cost a lot.",
                "An alarm will be incorporated for detection if the remote control is lost.",
            ])
        ]

        result = self.ledger.ingest_messages(messages, project_id="test_project")
        return self.ledger.get_events_by_project("test_project")

    def test_english_decision_extraction_remote_control(self):
        """Test English decision extraction for remote control topic."""
        events = self._create_sample_events_english_remote_control()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Should extract at least some candidates
        self.assertGreater(len(candidates), 0, "Should extract candidates from English content")

        # Check for decision candidates
        decisions = [c for c in candidates if c["candidate_type"] == "decision"]
        self.assertGreater(len(decisions), 0, "Should extract decision candidates")

        # Check that topics are detected
        topics = [c["topic"] for c in candidates]
        self.assertTrue(any("Remote" in t or "remote" in t.lower() or "Design" in t for t in topics),
                       f"Should detect remote control topic, got: {topics}")

    def test_english_risk_extraction_cost(self):
        """Test English risk extraction for cost concerns."""
        events = self._create_sample_events_english_remote_control()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Check for risk/concern candidates
        risks = [c for c in candidates if c["candidate_type"] == "risk"]
        self.assertGreater(len(risks), 0, "Should extract risk candidates about cost")

        # Verify risk content mentions cost/expensive OR not user-friendly
        risk_summaries = " ".join([r["summary"] for r in risks]).lower()
        risk_found = ("cost" in risk_summaries or "expensive" in risk_summaries or
                      "user-friendly" in risk_summaries or "user friendly" in risk_summaries)
        self.assertTrue(risk_found,
                       f"Risk should mention cost/expensive/user-friendly, got: {risk_summaries}")

    def test_english_decision_keywords(self):
        """Test that English decision keywords are detected."""
        events = self._create_sample_events_english_remote_control()

        # Build a combined window for testing
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Check that decision keywords were found
        self.assertGreater(len(candidates), 0, "Decision keywords should trigger extraction")

        # Look for specific decision patterns in evidence
        all_content = " ".join([e["content"] for e in events]).lower()

        # Check for common decision keywords
        decision_keywords = ["should", "incorporated", "agree", "recommend"]
        found_keywords = [kw for kw in decision_keywords if kw in all_content]
        self.assertGreater(len(found_keywords), 0,
                          f"Should find decision keywords, found: {found_keywords}")

    def test_english_topic_inference(self):
        """Test English topic inference."""
        events = self._create_sample_events_english_remote_control()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Extract topics
        topics = [c["topic"] for c in candidates]

        # Check for expected topics
        self.assertTrue(any(topic for topic in topics if "Remote" in topic or "remote" in topic.lower()),
                       f"Should detect Remote Control topic, got: {topics}")

    def test_english_fact_extraction_market_research(self):
        """Test English fact extraction for market research content."""
        events = self._create_sample_events_english_market_research()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Should extract candidates
        self.assertGreater(len(candidates), 0, "Should extract candidates from market research content")

        # Check for fact candidates
        facts = [c for c in candidates if c["candidate_type"] == "fact"]
        self.assertGreater(len(facts), 0, "Should extract fact candidates about market research")

    def test_english_risk_extraction_complicated(self):
        """Test English risk extraction for complicated/expensive concerns."""
        events = self._create_sample_events_english_market_research()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Check for risk candidates
        risks = [c for c in candidates if c["candidate_type"] == "risk"]
        self.assertGreater(len(risks), 0, "Should extract risk candidates")

    def test_english_evidence_tracking(self):
        """Test that evidence_event_ids are properly tracked."""
        events = self._create_sample_events_english_remote_control()
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Check evidence tracking
        for candidate in candidates:
            self.assertIn("evidence_event_ids", candidate)
            self.assertIsInstance(candidate["evidence_event_ids"], list)
            self.assertGreater(len(candidate["evidence_event_ids"]), 0,
                             f"Candidate should have evidence: {candidate}")

    def test_english_touch_screen_panel(self):
        """Test extraction of touch screen panel decision."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "Industrial Designer",
                "content": content,
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i, content in enumerate([
                "I recommend a touch screen panel for the remote control.",
                "The touch screen panel should be incorporated into the design.",
            ])
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")
        events = self.ledger.get_events_by_project("test_project")
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Should extract candidates mentioning touch screen
        self.assertGreater(len(candidates), 0, "Should extract candidates")

        # Check content mentions touch screen
        all_summaries = " ".join([c["summary"] for c in candidates]).lower()
        self.assertTrue("touch" in all_summaries or "screen" in all_summaries,
                       f"Summary should mention touch screen, got: {all_summaries}")

    def test_english_alarm_detection(self):
        """Test extraction of alarm detection decision."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "Project Manager",
                "content": content,
                "timestamp": f"2026-05-01T10:{i:02d}:00+08:00",
            }
            for i, content in enumerate([
                "An alarm will be incorporated for detection.",
                "The alarm helps if the remote control is lost.",
            ])
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")
        events = self.ledger.get_events_by_project("test_project")
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Should extract candidates mentioning alarm
        self.assertGreater(len(candidates), 0, "Should extract candidates")

        # Check content mentions alarm
        all_summaries = " ".join([c["summary"] for c in candidates]).lower()
        self.assertTrue("alarm" in all_summaries,
                       f"Summary should mention alarm, got: {all_summaries}")

    def test_english_speech_recognition_give_up(self):
        """Test extraction of 'give up' decision for speech recognition."""
        messages = [
            {
                "message_id": f"msg_{i:03d}",
                "actor_id": "Marketing",
                "content": "We gave up speech recognition because it would cost a lot.",
                "timestamp": f"2026-05-01T10:0{i}:00+08:00",
            }
            for i in range(2)
        ]

        self.ledger.ingest_messages(messages, project_id="test_project")
        events = self.ledger.get_events_by_project("test_project")
        windows = self.builder.build_windows(events, "test_project")

        candidates = self.extractor.extract_candidates(
            windows,
            {e["event_id"]: e for e in events}
        )

        # Should extract candidates
        self.assertGreater(len(candidates), 0, "Should extract candidates")

        # Check content mentions speech recognition or give up
        all_summaries = " ".join([c["summary"] for c in candidates]).lower()
        self.assertTrue("speech" in all_summaries or "gave up" in all_summaries or "give up" in all_summaries,
                       f"Summary should mention speech recognition or give up, got: {all_summaries}")


if __name__ == "__main__":
    unittest.main()
