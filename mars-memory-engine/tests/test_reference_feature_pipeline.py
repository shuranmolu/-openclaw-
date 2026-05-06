"""
Tests for reference-inspired planner, context, answer, session, and benchmarks.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from app.benchmark import run_consolidation_eval, run_quality_benchmark
from app.core.answerer import MemoryAnswerer
from app.core.context_assembler import ContextAssembler
from app.core.query_planner import QueryPlanner
from app.core.session_tracker import SessionTracker
from app.service import MarsService
from app.storage.db import init_db


class TestReferenceFeaturePipeline(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.service = None

    def tearDown(self):
        if self.service is not None:
            self.service.db.close()
        shutil.rmtree(self.temp_dir)

    def test_query_planner_detects_risk_intent(self):
        plan = QueryPlanner().plan("What is the risk with OpenClaw evidence cards?", top_k=7)

        self.assertEqual(plan.query_type, "risk_lookup")
        self.assertIn("risk", plan.preferred_types)
        self.assertEqual(plan.top_k, 7)

    def test_answerer_composes_from_primary_memory(self):
        plan = QueryPlanner().plan("why use OpenClaw architecture")
        bundle = MemoryAnswerer().compose(plan, [
            {
                "memory_id": "m1",
                "memory_type": "decision",
                "topic": "architecture",
                "title": "Use OpenClaw",
                "content": "Use OpenClaw tool flow for decision cards.",
                "score": 0.9,
                "rationale": ["It preserves evidence."],
            }
        ])

        self.assertEqual(bundle["primary_memory_id"], "m1")
        self.assertIn("OpenClaw", bundle["answer"])
        self.assertIn("Basis", bundle["answer"])

    def test_context_assembler_adds_bridge_events(self):
        events = {
            "e1": {"event_id": "e1", "content": "First window context."},
            "e2": {"event_id": "e2", "content": "Bridge this plan."},
            "e3": {"event_id": "e3", "content": "Second window says use it."},
        }
        windows = [
            {"window_id": "w1", "event_ids": ["e1", "e2"], "topic_hint": "architecture"},
            {"window_id": "w2", "event_ids": ["e3"], "topic_hint": "architecture"},
        ]

        contexts = ContextAssembler(bridge_count=1).assemble(windows, events)

        self.assertEqual(contexts[1]["bridge_event_ids"], ["e2"])
        self.assertEqual(contexts[1]["topic_history"], ["architecture"])

    def test_session_tracker_marks_resume(self):
        events = [
            {
                "event_id": "e1",
                "content": "OpenClaw plugin architecture and memory engine.",
                "valid_time_start": "2026-05-01T10:00:00+08:00",
            },
            {
                "event_id": "e2",
                "content": "Deadline and release schedule for Friday.",
                "valid_time_start": "2026-05-01T10:01:00+08:00",
            },
            {
                "event_id": "e3",
                "content": "Back to OpenClaw plugin architecture evidence cards.",
                "valid_time_start": "2026-05-01T10:02:00+08:00",
            },
        ]
        annotations = SessionTracker().annotate_batch(events)

        self.assertEqual(annotations["e3"]["topic_state"], "resume")

    def test_service_search_returns_query_plan_and_answer_bundle(self):
        db = init_db(str(self.db_path.resolve()), force=True)
        db.close()
        self.service = MarsService(str(self.db_path.resolve()))
        self.service.memory_store.create_memory(
            memory_type="risk",
            topic="risk",
            title="Evidence missing risk",
            content="If evidence is missing, decision cards require review before push.",
            project_id="planner_project",
            status="active",
        )

        result = self.service.mars_search(
            project_id="planner_project",
            query="What risk exists for evidence cards?",
            top_k=3,
        )

        self.assertEqual(result["query_plan"]["query_type"], "risk_lookup")
        self.assertIn("answer_bundle", result)
        self.assertGreaterEqual(result["total_retrieved"], 1)

    def test_benchmark_runners_write_reports(self):
        report_dir = Path(self.temp_dir) / "reports"
        benchmark = run_quality_benchmark(output_dir=str(report_dir))
        consolidation = run_consolidation_eval(output_dir=str(report_dir))

        self.assertGreaterEqual(benchmark["total"], 1)
        self.assertGreaterEqual(consolidation["total"], 1)
        self.assertTrue(Path(benchmark["report_json"]).exists())
        self.assertTrue(Path(consolidation["report_json"]).exists())


if __name__ == "__main__":
    unittest.main()
