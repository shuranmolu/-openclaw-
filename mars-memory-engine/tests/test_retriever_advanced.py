"""
Tests for advanced retriever functionality.

Tests Chinese compound query handling and similar decision classification.
"""

import unittest
import tempfile
import shutil
from pathlib import Path

from app.storage.db import init_db
from app.service import MarsService
from app.core.retriever import MemoryRetriever
from app.storage.memory_store import MemoryStore


class TestChineseCompoundQuery(unittest.TestCase):
    """Test cases for Chinese compound query handling."""

    def setUp(self):
        """Set up test database and retriever."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.memory_store = MemoryStore(self.db)
        self.retriever = MemoryRetriever(self.memory_store)

        # Create test memories
        self._create_test_memories()

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def _create_test_memories(self):
        """Create test memories for compound query testing."""
        # Create decision memories with different topics
        self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="前端技术栈决策",
            content="采用 React + TypeScript 作为主要前端技术栈",
            project_id="test_project",
            status="active",
        )

        self.memory_store.create_memory(
            memory_type="decision",
            topic="行动项",
            title="下周行动项",
            content="完成技术方案评审，启动项目开发",
            project_id="test_project",
            status="active",
        )

        self.memory_store.create_memory(
            memory_type="decision",
            topic="技术路线",
            title="后端技术栈决策",
            content="采用 Python + FastAPI 作为后端技术栈",
            project_id="test_project",
            status="active",
        )

    def test_split_chinese_compound_query_with_and(self):
        """Test splitting compound query with 'and' pattern."""
        query = "技术路线和行动项"
        sub_queries = self.retriever._split_chinese_compound_query(query)

        self.assertEqual(len(sub_queries), 2)
        self.assertIn("技术路线", sub_queries)
        self.assertIn("行动项", sub_queries)

    def test_split_chinese_compound_query_with_comma(self):
        """Test splitting compound query with comma pattern."""
        query = "决策、风险和问题"
        sub_queries = self.retriever._split_chinese_compound_query(query)

        self.assertGreaterEqual(len(sub_queries), 2)
        self.assertTrue(any("决策" in q for q in sub_queries))
        self.assertTrue(any("风险" in q or "问题" in q for q in sub_queries))

    def test_split_chinese_compound_query_english(self):
        """Test splitting English compound query."""
        query = "technology and design"
        sub_queries = self.retriever._split_chinese_compound_query(query)

        self.assertEqual(len(sub_queries), 2)
        self.assertIn("technology", sub_queries)
        self.assertIn("design", sub_queries)

    def test_split_single_query(self):
        """Test that single query returns as-is."""
        query = "技术路线"
        sub_queries = self.retriever._split_chinese_compound_query(query)

        self.assertEqual(len(sub_queries), 1)
        self.assertEqual(sub_queries[0], "技术路线")

    def test_search_compound_query(self):
        """Test searching with compound query."""
        result = self.retriever.search(
            project_id="test_project",
            query="技术路线和行动项",
            top_k=10,
        )

        # Should find memories from both topics
        self.assertGreaterEqual(result["total_retrieved"], 2)
        self.assertIn("compound_queries", result)
        self.assertEqual(len(result["compound_queries"]), 2)
        self.assertIn("retrieval_log_id", result)

        logs = self.memory_store.list_retrieval_logs(project_id="test_project", limit=5)
        self.assertGreaterEqual(len(logs), 1)
        self.assertEqual(logs[0]["log_id"], result["retrieval_log_id"])
        self.assertGreaterEqual(len(logs[0]["selected_memory_ids"]), 1)

    def test_extract_keywords_chinese(self):
        """Test keyword extraction for Chinese text."""
        query = "技术路线采用 TypeScript"
        keywords = self.retriever._extract_keywords(query)

        # Should extract Chinese bigrams
        self.assertTrue(any("技术" in k or "路线" in k for k in keywords))
        # Should filter out stop words
        self.assertFalse(any("采用" in k for k in keywords))


    def test_local_vector_candidates(self):
        """Test local vector recall can score related memories."""
        candidates = self.retriever._semantic_candidates(
            project_id="test_project",
            query="python backend",
            time_scope="current",
            memory_types=["decision"],
            top_k=3,
        )

        self.assertGreaterEqual(len(candidates), 1)
        self.assertTrue(any("FastAPI" in item.get("content", "") for item in candidates))
        self.assertTrue(all("semantic_score" in item for item in candidates))


class TestSimilarDecisionClassification(unittest.TestCase):
    """Test cases for similar decision classification."""

    def setUp(self):
        """Set up test database and retriever."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.memory_store = MemoryStore(self.db)
        self.retriever = MemoryRetriever(self.memory_store)

        # Create test decisions
        self._create_test_decisions()

    def tearDown(self):
        """Clean up test database."""
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def _create_test_decisions(self):
        """Create test decisions for classification testing."""
        # Decision about TypeScript
        self.memory_store.create_memory(
            memory_type="decision",
            topic="前端技术栈",
            title="采用 TypeScript",
            content="项目采用 TypeScript 作为主要开发语言",
            project_id="test_project",
            status="active",
        )

        # Decision about React
        self.memory_store.create_memory(
            memory_type="decision",
            topic="前端技术栈",
            title="采用 React",
            content="前端框架选择 React",
            project_id="test_project",
            status="active",
        )

        # Decision about NOT using jQuery
        self.memory_store.create_memory(
            memory_type="decision",
            topic="前端技术栈",
            title="不使用 jQuery",
            content="项目不使用 jQuery，改用现代框架",
            project_id="test_project",
            status="active",
        )

    def test_find_similar_decisions(self):
        """Test finding similar decisions."""
        result = self.retriever.find_similar_decisions(
            project_id="test_project",
            query="TypeScript 开发语言",
            top_k=5,
        )

        self.assertIn("similar_decisions", result)
        self.assertGreater(len(result["similar_decisions"]), 0)

        # Check structure of similar decisions
        for sim in result["similar_decisions"]:
            self.assertIn("relation", sim)
            self.assertIn("reason", sim)
            self.assertIn("confidence", sim)
            self.assertIn("decision", sim)
            self.assertIn(sim["relation"], ["duplicate", "update", "conflict", "new"])

    def test_classify_duplicate_decision(self):
        """Test classification of duplicate decisions."""
        existing = self.memory_store.search_memories(
            project_id="test_project",
            query="TypeScript",
            memory_types=["decision"],
            limit=1,
        )

        if existing:
            relation, reason, confidence = self.retriever._classify_decision_relation(
                query="采用 TypeScript 开发",
                text="我们决定使用 TypeScript",
                existing_memory=existing[0],
            )

            # Should be classified as duplicate or update due to high keyword overlap
            self.assertIn(relation, ["duplicate", "update"])

    def test_classify_conflict_decision(self):
        """Test classification of conflicting decisions."""
        existing = self.memory_store.search_memories(
            project_id="test_project",
            query="jQuery",
            memory_types=["decision"],
            limit=1,
        )

        if existing:
            relation, reason, confidence = self.retriever._classify_decision_relation(
                query="采用 jQuery 框架",
                text="决定使用 jQuery",
                existing_memory=existing[0],
            )

            # Existing says "不使用 jQuery", new says "采用 jQuery" - should be conflict
            self.assertEqual(relation, "conflict")

    def test_classify_new_decision(self):
        """Test classification of new decisions."""
        existing = self.memory_store.search_memories(
            project_id="test_project",
            query="TypeScript",
            memory_types=["decision"],
            limit=1,
        )

        if existing:
            relation, reason, confidence = self.retriever._classify_decision_relation(
                query="使用 Docker 容器化部署",
                text="部署采用 Docker 方案",
                existing_memory=existing[0],
            )

            # Should be classified as new due to low keyword overlap
            self.assertEqual(relation, "new")

    def test_calculate_text_similarity(self):
        """Test text similarity calculation."""
        # High similarity
        sim1 = self.retriever._calculate_text_similarity(
            "采用 TypeScript 开发",
            "使用 TypeScript 作为开发语言"
        )
        self.assertGreater(sim1, 0.3)

        # Low similarity
        sim2 = self.retriever._calculate_text_similarity(
            "TypeScript 开发",
            "Docker 部署"
        )
        self.assertLess(sim2, 0.2)

        # Empty input
        sim3 = self.retriever._calculate_text_similarity("", "")
        self.assertEqual(sim3, 0.0)


class TestDecisionCardWithLifecycle(unittest.TestCase):
    """Test cases for decision card with lifecycle fields."""

    def setUp(self):
        """Set up test database and service."""
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = init_db(str(self.db_path.resolve()), force=True)
        self.service = MarsService(str(self.db_path.resolve()))

    def tearDown(self):
        """Clean up test database."""
        self.service.db.close()
        shutil.rmtree(self.temp_dir)

    def test_process_command_builds_decision_card(self):
        """Test that processing a command builds a decision card."""
        # Create some test data first
        self.service.mars_ingest_text(
            project_id="test_project",
            text="会议决定采用 React 作为前端框架。这是一个重要的技术选择。",
            title="Test Doc",
        )

        result = self.service.mars_process_command(
            project_id="test_project",
            command_text="决定采用 React",
            query="React",
        )

        self.assertIn("decision_card", result)
        card = result["decision_card"]

        # Check lifecycle fields
        self.assertIn("lifecycle", card)
        lifecycle = card["lifecycle"]

        self.assertIn("status", lifecycle)
        self.assertIn("similar_decisions", lifecycle)
        self.assertIn("recommended_action", lifecycle)
        self.assertIn("requires_confirmation", lifecycle)

        # Status should be one of the valid values
        self.assertIn(lifecycle["status"], ["new", "update", "conflict", "duplicate"])

    def test_lifecycle_status_new_when_no_similar(self):
        """Test that lifecycle status is 'new' when no similar decisions exist."""
        result = self.service.mars_process_command(
            project_id="test_project",
            command_text="决定采用 Vue 3 框架",
            query="Vue",
        )

        card = result["decision_card"]
        lifecycle = card["lifecycle"]

        # Should be new since no similar decisions
        self.assertEqual(lifecycle["status"], "new")
        self.assertEqual(lifecycle["recommended_action"], "create_new")
        self.assertTrue(lifecycle["requires_confirmation"])

    def test_agent_lifecycle_decision_overrides_heuristic_preview(self):
        """OpenClaw can provide the semantic lifecycle judgment for the card."""
        result = self.service.mars_process_command(
            project_id="test_project",
            command_text="更新技术路线",
            context_text="第二周期决定补充主动唤醒能力。",
            query="主动唤醒",
            agent_summary="第一周期：确定三模块架构。第二周期：补充主动唤醒能力。",
            agent_lifecycle_decision={
                "status": "update",
                "reason": "这是对既有技术路线的补充，不是全新决策。",
                "target_memory_id": "mem_existing",
                "recommended_action": "update_existing",
                "confidence": 0.82,
            },
        )

        lifecycle = result["decision_card"]["lifecycle"]
        self.assertEqual(lifecycle["status"], "update")
        self.assertEqual(lifecycle["recommended_action"], "update_existing")
        self.assertEqual(lifecycle["agent_decision"]["target_memory_id"], "mem_existing")
        self.assertTrue(result["agent_summary_used"])
        self.assertTrue(result["agent_lifecycle_decision_used"])

    def test_agent_structured_card_fields_are_preserved(self):
        """Decision cards preserve decision reasons, objections, conclusions, phase and dates."""
        result = self.service.mars_process_command(
            project_id="test_project",
            command_text="生成结构化决策卡",
            context_text="第一周期确认三模块架构，第二周期补充主动推送。",
            query="主动推送",
            agent_lifecycle_decision={
                "status": "update",
                "reason": "补充已有方案",
                "confidence": 0.8,
            },
            agent_structured_card={
                "decision_items": [
                    {
                        "decision": "采用主动推送历史决策卡",
                        "reason": "避免重复讨论和遗忘旧约束",
                        "objection": "需要控制打扰频率",
                        "conclusion": "使用两阶段触发机制",
                        "phase": "第二周期",
                        "time_point": "4.26-4.30",
                    }
                ],
                "reasons": ["避免重复讨论"],
                "objections": ["推送可能打扰用户"],
                "conclusions": ["命中相关历史决策时推送卡片"],
                "project_phase": "第二周期",
                "time_points": ["4.26-4.30"],
                "topic_links": ["主动唤醒", "历史决策卡片"],
            },
        )

        card = result["decision_card"]
        self.assertTrue(result["agent_structured_card_used"])
        self.assertEqual(card["project_phase"], "第二周期")
        self.assertIn("4.26-4.30", card["time_points"])
        self.assertEqual(card["decision_items"][0]["reason"], "避免重复讨论和遗忘旧约束")
        self.assertIn("推送可能打扰用户", card["objections"])


if __name__ == "__main__":
    unittest.main()
