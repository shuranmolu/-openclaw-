"""
Tests for topic splitting, candidate governance, consolidation, and benchmark flow.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.core.consolidator import MemoryConsolidator
from app.core.post_processor import CandidatePostProcessor
from app.core.topic_tracker import TopicTracker
from app.core.window_builder import WindowBuilder
from app.service import MarsService
from app.storage.db import init_db


class TestMemoryQualityPipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.service = None

    def tearDown(self):
        if self.service is not None:
            self.service.db.close()
        shutil.rmtree(self.temp_dir)

    def test_topic_tracker_splits_mixed_chat_windows(self):
        events = [
            {
                "event_id": "e1",
                "content": "OpenClaw plugin architecture should keep MARS as a separate engine.",
                "valid_time_start": "2026-05-01T10:00:00+08:00",
            },
            {
                "event_id": "e2",
                "content": "The memory module architecture will expose a decision card tool.",
                "valid_time_start": "2026-05-01T10:01:00+08:00",
            },
            {
                "event_id": "e3",
                "content": "The deadline is Friday and the release schedule is tight.",
                "valid_time_start": "2026-05-01T10:02:00+08:00",
            },
            {
                "event_id": "e4",
                "content": "Next milestone should be before the May release date.",
                "valid_time_start": "2026-05-01T10:03:00+08:00",
            },
        ]
        annotations = TopicTracker().annotate_batch(events)
        windows = WindowBuilder(time_window_minutes=30, max_messages=20).build_windows(
            events,
            "project_a",
            topic_annotations=annotations,
        )

        self.assertGreaterEqual(len(windows), 2)
        self.assertEqual(windows[0]["topic_hint"], "architecture")
        self.assertEqual(windows[1]["topic_hint"], "timeline")
        self.assertEqual(windows[0]["split_reason"], "topic_shift")

    def test_post_processor_drops_weak_noise_and_marks_review(self):
        candidates = [
            {
                "candidate_id": "c1",
                "candidate_type": "decision",
                "topic": "architecture",
                "summary": "We decided to use OpenClaw plugin architecture.",
                "confidence": 0.82,
                "evidence_event_ids": ["e1"],
                "need_human_confirm": False,
            },
            {
                "candidate_id": "c2",
                "candidate_type": "risk",
                "topic": "risk",
                "summary": "Maybe?",
                "confidence": 0.4,
                "evidence_event_ids": [],
                "need_human_confirm": True,
            },
        ]
        kept, dropped = CandidatePostProcessor().process_candidates(candidates)

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(kept[0]["topic_normalized"], "architecture")
        self.assertEqual(dropped[0]["drop_reason"], "low_confidence")

    def test_consolidator_detects_update_and_support(self):
        consolidator = MemoryConsolidator()
        items = [
            {
                "memory_id": "m1",
                "memory_type": "decision",
                "topic": "architecture",
                "content": "We decided to use plugin architecture for Feishu memory cards.",
                "evidence_event_ids": ["e1"],
            },
            {
                "memory_id": "m2",
                "memory_type": "decision",
                "topic": "architecture",
                "content": "Update: replace the Feishu memory card plugin with OpenClaw tool flow.",
                "evidence_event_ids": ["e2"],
            },
            {
                "memory_id": "m3",
                "memory_type": "fact",
                "topic": "architecture",
                "content": "OpenClaw plugin architecture keeps evidence event ids on the memory card.",
                "evidence_event_ids": ["e3"],
            },
        ]
        result = consolidator.propose(items)
        relations = {proposal["relation"] for proposal in result["proposals"]}

        self.assertIn("update", relations)
        self.assertIn("support", relations)

    def test_digest_returns_quality_benchmark_fields(self):
        db = init_db(str(self.db_path.resolve()), force=True)
        db.close()
        self.service = MarsService(str(self.db_path.resolve()))
        self.service.mars_ingest_messages(
            project_id="benchmark_project",
            messages=[
                {
                    "message_id": "msg_1",
                    "actor_id": "u1",
                    "content": "We decided to use OpenClaw plugin architecture for the decision card.",
                    "timestamp": "2026-05-01T10:00:00+08:00",
                },
                {
                    "message_id": "msg_2",
                    "actor_id": "u2",
                    "content": "The rationale is that OpenClaw can keep evidence and tool calls visible.",
                    "timestamp": "2026-05-01T10:01:00+08:00",
                },
                {
                    "message_id": "msg_3",
                    "actor_id": "u3",
                    "content": "Risk: if evidence is missing, the card must require review before push.",
                    "timestamp": "2026-05-01T10:02:00+08:00",
                },
            ],
        )
        result = self.service.mars_digest("benchmark_project", auto_commit=False)

        self.assertGreaterEqual(len(result["windows"]), 1)
        self.assertIn("topic_annotations", result)
        self.assertIn("dropped_candidates", result)
        self.assertIn("consolidation", result)
        self.assertGreaterEqual(len(result["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
