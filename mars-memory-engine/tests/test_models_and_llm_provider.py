import tempfile
import unittest

from app.llm import MockLLMProvider, get_llm_provider
from app.llm.provider import _loads_json_object
from app.models import DecisionCard, LifecycleDecision
from app.service import MarsService
from app.storage.db import init_db


class ModelsAndProviderTest(unittest.TestCase):
    def test_decision_card_normalizes_nested_payload(self):
        card = DecisionCard.model_validate({
            "title": "Project decision",
            "summary": ["Use active command trigger"],
            "decisions": ["Use OpenClaw to judge update vs new"],
            "decision_items": [{
                "decision": "Keep lifecycle decision in OpenClaw",
                "reason": "It can inspect tool output and decide",
                "objection": "",
                "conclusion": "Return structured lifecycle status",
            }],
            "confidence": 1.7,
            "lifecycle": {
                "status": "update",
                "heuristic_status": "new",
                "agent_decision": {
                    "status": "update",
                    "reason": "Similar previous card exists",
                    "target_memory_id": "mem-1",
                    "recommended_action": "update_existing",
                    "confidence": "0.82",
                },
                "recommended_action": "update_existing",
            },
        })

        payload = card.to_dict()

        self.assertEqual(payload["confidence"], 1.0)
        self.assertEqual(payload["decision_items"][0]["decision"], "Keep lifecycle decision in OpenClaw")
        self.assertEqual(payload["lifecycle"]["agent_decision"]["confidence"], 0.82)

    def test_provider_boundary_returns_typed_results(self):
        provider = get_llm_provider("mock")

        self.assertIsInstance(provider, MockLLMProvider)

        extracted = provider.extract_memories([
            {"event_id": "e1", "text": "决定采用主动唤醒命令生成决策卡。"},
        ])
        relation = provider.judge_relation(
            {"summary": "same text"},
            [{"memory_id": "m1", "summary": "same text"}],
        )
        card = provider.generate_decision_card({
            "title": "Decision card",
            "summary": ["A"],
            "decisions": ["A"],
            "lifecycle": {"status": "new", "heuristic_status": "new"},
        })
        score = provider.evaluate_card(card.to_dict())

        self.assertEqual(extracted[0]["memory_type"], "decision")
        self.assertIsInstance(relation, LifecycleDecision)
        self.assertEqual(relation.status, "duplicate")
        self.assertIsInstance(card, DecisionCard)
        self.assertTrue(score.passed)

    def test_provider_json_loader_handles_markdown(self):
        parsed = _loads_json_object("""```json
{"score": 0.75, "passed": true}
```""")

        self.assertEqual(parsed["score"], 0.75)
        self.assertTrue(parsed["passed"])

    def test_service_returns_typed_decision_card_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = f"{tmpdir}/mars.db"
            init_db(db_path, force=True)
            service = MarsService(db_path=db_path)
            try:
                result = service.mars_process_command(
                    project_id="typed_project",
                    command_text="根据这段内容生成决策卡",
                    context_text="张三：我们决定复赛截止时间是 5/7 12:00。李四：原因是组委会统一安排。",
                    source_id="doc-1",
                    auto_commit=False,
                    agent_lifecycle_decision={
                        "status": "update",
                        "reason": "OpenClaw judged it updates an existing timeline",
                        "confidence": 3,
                    },
                )
                evaluation = service.mars_evaluate_decision_card(result["decision_card"])
            finally:
                service.db.close()

        card = result["decision_card"]

        self.assertEqual(card["project_id"], "typed_project")
        self.assertIn("evidence_chain", card)
        self.assertTrue(card["evidence_chain"]["evidence_items"])
        self.assertTrue(card["evidence_chain"]["coverage"]["has_source_quote"])
        self.assertIn(card["lifecycle"]["status"], {"new", "update", "conflict", "duplicate"})
        self.assertLessEqual(card["lifecycle"]["agent_decision"]["confidence"], 1.0)
        self.assertIn("score", evaluation)


if __name__ == "__main__":
    unittest.main()
