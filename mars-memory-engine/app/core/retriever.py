"""
Memory Retriever for MARS Memory Engine.

Provides search and retrieval functionality for memories.
"""

import math
import re
import time
from typing import Any, Dict, List, Optional

from ..storage.memory_store import MemoryStore


class MemoryRetriever:
    """Retrieves memories based on queries and filters."""

    # Chinese common words to filter out
    CHINESE_STOP_WORDS = {
        "的", "了", "和", "与", "或", "在", "从", "到", "对", "把", "被", "让",
        "是", "有", "没有", "不", "也", "都", "很", "非常", "比较", "最", "更",
        "这", "那", "这个", "那个", "这些", "那些", "什么", "怎么", "如何", "为什么",
        "可以", "能够", "应该", "需要", "要", "会", "将", "就", "还", "又",
        "说", "问", "答", "讨论", "表示", "认为", "觉得", "决定", "选择",
        "关于", "相关", "根据", "按照", "通过", "由于", "因为", "所以", "但是",
        "然后", "接着", "最后", "首先", "其次", "总之", "因此", "另外", "此外",
        "采用", "使用", "进行",
    }

    # English common words
    ENGLISH_STOP_WORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
        "be", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "that", "which", "who",
        "what", "when", "where", "why", "how", "it", "its", "this", "these",
        "those", "about", "into", "over", "after", "so", "if", "out", "up",
    }

    def __init__(self, memory_store: MemoryStore):
        """Initialize retriever.

        Args:
            memory_store: MemoryStore instance.
        """
        self.memory_store = memory_store

    def _split_chinese_compound_query(self, query: str) -> List[str]:
        """Split Chinese compound query into sub-queries.

        For example: "技术路线和行动项" -> ["技术路线", "行动项"]
                    "决策、风险和问题" -> ["决策", "风险", "问题"]

        Args:
            query: Search query text.

        Returns:
            List of sub-queries.
        """
        # Chinese separators: 和、与、或、、、，、,
        # Check if any Chinese separator exists
        chinese_separators = ["和", "与", "或", "、", "，", "；", ","]
        has_chinese_sep = any(sep in query for sep in chinese_separators)

        if has_chinese_sep:
            # Split by all Chinese separators (try most specific first)
            # Use regex to split by any Chinese conjunction
            parts = re.split(r"(?:和|与|或|、|，|；|,)", query)
            sub_queries = [p.strip() for p in parts if p.strip()]
            if len(sub_queries) > 1:
                return sub_queries

        # Check for English compound patterns with "and"
        if " and " in query.lower():
            parts = re.split(r"\s+and\s+", query, flags=re.IGNORECASE)
            sub_queries = [p.strip() for p in parts if p.strip()]
            if len(sub_queries) > 1:
                return sub_queries

        # Return original query if no compound pattern found
        return [query]

    def _extract_keywords(self, query: str) -> List[str]:
        """Extract meaningful keywords from a query.

        Args:
            query: Search query text.

        Returns:
            List of keywords for searching.
        """
        # Extract English words
        english_words = re.findall(r"\b[a-zA-Z]{3,}\b", query.lower())

        # Extract Chinese bigrams (2-character sequences)
        chinese_chars = re.findall(r"[\u4e00-\u9fa5]", query)
        chinese_bigrams = []
        for i in range(len(chinese_chars) - 1):
            chinese_bigrams.append(chinese_chars[i] + chinese_chars[i + 1])

        # Combine and filter
        all_keywords = english_words + chinese_bigrams

        # Filter out stop words
        stop_words = self.CHINESE_STOP_WORDS | self.ENGLISH_STOP_WORDS
        keywords = [w for w in all_keywords if w not in stop_words]

        return keywords

    def search(
        self,
        project_id: str,
        query: str,
        time_scope: str = "current",
        memory_types: Optional[List[str]] = None,
        top_k: int = 5,
        include_evidence: bool = True,
    ) -> Dict[str, Any]:
        """Search memories with filters.

        Supports Chinese compound queries like "技术路线和行动项" which will be
        split into ["技术路线", "行动项"] and results merged.

        Args:
            project_id: Project ID.
            query: Search query text.
            time_scope: Time scope (current, all, history).
            memory_types: Optional list of memory types to filter.
            top_k: Maximum number of results.
            include_evidence: Whether to include source evidence.

        Returns:
            Dict with answer, memories, total_retrieved, latency_ms.
        """
        start_time = time.time()

        # Check for compound query
        sub_queries = self._split_chinese_compound_query(query)

        # If single query, use original logic
        if len(sub_queries) == 1:
            memories = self._search_single(
                project_id, query, time_scope, memory_types, top_k
            )
        else:
            # Merge results from multiple sub-queries
            memories = self._search_and_merge(
                project_id, sub_queries, time_scope, memory_types, top_k
            )

        # Build answer from memories
        answer = self._build_answer(query, memories)

        # Include evidence if requested
        if include_evidence:
            for memory in memories:
                memory["evidence"] = self._format_evidence(memory.get("sources", []))

        latency_ms = int((time.time() - start_time) * 1000)
        log_id = self.memory_store.log_retrieval(
            query=query,
            project_id=project_id,
            retrieved_memory_ids=[m.get("memory_id", "") for m in memories if m.get("memory_id")],
            selected_memory_ids=[m.get("memory_id", "") for m in memories[:top_k] if m.get("memory_id")],
            score_items=[
                {
                    "memory_id": m.get("memory_id", ""),
                    "score": m.get("score", 0),
                    "matched_query": m.get("matched_query", query),
                    "title": m.get("title", ""),
                }
                for m in memories
            ],
            latency_ms=latency_ms,
            time_scope=time_scope,
            top_k=top_k,
            status_filter="active" if time_scope == "current" else "all",
            retrieval_method="keyword_local_vector_hybrid",
        )

        return {
            "answer": answer,
            "memories": memories,
            "total_retrieved": len(memories),
            "latency_ms": latency_ms,
            "retrieval_log_id": log_id,
            "compound_queries": sub_queries if len(sub_queries) > 1 else None,
        }

    def _search_single(
        self,
        project_id: str,
        query: str,
        time_scope: str,
        memory_types: Optional[List[str]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Search for a single query.

        Args:
            project_id: Project ID.
            query: Search query text.
            time_scope: Time scope.
            memory_types: Optional list of memory types to filter.
            top_k: Maximum number of results.

        Returns:
            List of memory dicts.
        """
        # Extract keywords for better search
        keywords = self._extract_keywords(query)

        # Determine search query
        search_query = query

        # For long queries or queries with few keywords, use simplified search
        if len(query) > 50 and keywords:
            # Try with first 2 keywords for better matching
            search_query = " ".join(keywords[:2])

        # Perform search
        memories = self.memory_store.search_memories(
            project_id=project_id,
            query=search_query,
            memory_types=memory_types,
            status="active" if time_scope == "current" else "all",
            time_scope=time_scope,
            limit=top_k,
        )

        # If no results and we have keywords, try with just the first keyword
        if not memories and keywords:
            memories = self.memory_store.search_memories(
                project_id=project_id,
                query=keywords[0],
                memory_types=memory_types,
                status="active" if time_scope == "current" else "all",
                time_scope=time_scope,
                limit=top_k,
            )

        if len(memories) < top_k:
            seen_ids = {memory.get("memory_id") for memory in memories}
            for memory in self._semantic_candidates(project_id, query, time_scope, memory_types, top_k):
                memory_id = memory.get("memory_id")
                if memory_id and memory_id not in seen_ids:
                    memories.append(memory)
                    seen_ids.add(memory_id)
                if len(memories) >= top_k:
                    break

        memories.sort(
            key=lambda item: (
                -float(item.get("score", 0) or 0),
                -float(item.get("semantic_score", 0) or 0),
                -float(item.get("importance", 0) or 0),
            )
        )
        return memories[:top_k]

    def _vector_tokens(self, text: str) -> List[str]:
        """Build local lexical vector tokens for lightweight semantic recall."""
        if not text:
            return []
        lower = text.lower()
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}", lower)
        cjk_terms = re.findall(r"[\u4e00-\u9fa5]{2,}", text)
        cjk_bigrams = []
        chars = re.findall(r"[\u4e00-\u9fa5]", text)
        for i in range(len(chars) - 1):
            cjk_bigrams.append(chars[i] + chars[i + 1])
        return [
            token for token in tokens + cjk_terms + cjk_bigrams
            if token not in self.ENGLISH_STOP_WORDS and token not in self.CHINESE_STOP_WORDS
        ]

    def _text_vector(self, text: str) -> Dict[str, float]:
        vector: Dict[str, float] = {}
        for token in self._vector_tokens(text):
            vector[token] = vector.get(token, 0.0) + 1.0
        return vector

    def _cosine_similarity(self, left: Dict[str, float], right: Dict[str, float]) -> float:
        if not left or not right:
            return 0.0
        shared = set(left) & set(right)
        dot = sum(left[token] * right[token] for token in shared)
        left_norm = math.sqrt(sum(value * value for value in left.values()))
        right_norm = math.sqrt(sum(value * value for value in right.values()))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return dot / (left_norm * right_norm)

    def _semantic_candidates(
        self,
        project_id: str,
        query: str,
        time_scope: str,
        memory_types: Optional[List[str]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Recall memories by local vector similarity when exact LIKE search misses."""
        query_vector = self._text_vector(query)
        if not query_vector:
            return []
        candidates = self.memory_store.search_memories(
            project_id=project_id,
            query=None,
            memory_types=memory_types,
            status="active" if time_scope == "current" else "all",
            time_scope=time_scope,
            limit=max(top_k * 8, 40),
        )
        ranked = []
        for memory in candidates:
            text = " ".join([
                str(memory.get("title") or ""),
                str(memory.get("topic") or ""),
                str(memory.get("content") or ""),
            ])
            score = self._cosine_similarity(query_vector, self._text_vector(text))
            if score > 0:
                memory = dict(memory)
                memory["semantic_score"] = score
                memory["score"] = max(float(memory.get("score", 0) or 0), round(score, 4))
                memory["retrieval_match"] = "local_vector"
                ranked.append(memory)
        ranked.sort(key=lambda item: (-item.get("semantic_score", 0), -item.get("importance", 0)))
        return ranked[:top_k]

    def _search_and_merge(
        self,
        project_id: str,
        sub_queries: List[str],
        time_scope: str,
        memory_types: Optional[List[str]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """Search for multiple sub-queries and merge results.

        Args:
            project_id: Project ID.
            sub_queries: List of sub-query strings.
            time_scope: Time scope.
            memory_types: Optional list of memory types to filter.
            top_k: Maximum results per sub-query.

        Returns:
            Merged and deduplicated list of memory dicts.
        """
        seen_ids = set()
        merged_memories = []

        for sub_query in sub_queries:
            memories = self._search_single(
                project_id, sub_query, time_scope, memory_types, top_k
            )
            for memory in memories:
                memory_id = memory.get("memory_id")
                if memory_id and memory_id not in seen_ids:
                    seen_ids.add(memory_id)
                    # Add sub_query match info
                    memory["matched_query"] = sub_query
                    merged_memories.append(memory)

        # Sort by score/importance and limit total results
        merged_memories.sort(
            key=lambda m: (
                m.get("score", 0) * -1,
                m.get("importance", 0) * -1,
            )
        )
        return merged_memories[:top_k * len(sub_queries)]

    def find_similar_decisions(
        self,
        project_id: str,
        query: str,
        text: Optional[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Find similar decisions and classify their relationship.

        Uses heuristic matching based on keywords, topic, and content overlap.

        Args:
            project_id: Project ID.
            query: Search query for finding similar decisions.
            text: Optional full text for more detailed comparison.
            top_k: Maximum number of similar decisions to return.

        Returns:
            Dict with similar_decisions list containing:
                - memory_id: ID of the similar decision
                - relation: "duplicate", "update", "conflict", or "new"
                - reason: Explanation of the relationship
                - confidence: Confidence score 0.0-1.0
                - decision: The decision memory object
        """
        # Search for existing decisions
        memories = self.memory_store.search_memories(
            project_id=project_id,
            query=query,
            memory_types=["decision"],
            status="all",  # Include all statuses for comparison
            time_scope="all",
            limit=top_k * 2,  # Get more to filter
        )
        if not memories:
            seen_ids = set()
            for keyword in self._extract_keywords(query)[:6]:
                for memory in self.memory_store.search_memories(
                    project_id=project_id,
                    query=keyword,
                    memory_types=["decision"],
                    status="all",
                    time_scope="all",
                    limit=top_k * 2,
                ):
                    memory_id = memory.get("memory_id")
                    if memory_id and memory_id not in seen_ids:
                        seen_ids.add(memory_id)
                        memories.append(memory)

        similar_decisions = []

        for memory in memories:
            relation, reason, confidence = self._classify_decision_relation(
                query, text, memory
            )

            similar_decisions.append({
                "memory_id": memory.get("memory_id"),
                "relation": relation,
                "reason": reason,
                "confidence": confidence,
                "decision": {
                    "memory_id": memory.get("memory_id"),
                    "title": memory.get("title"),
                    "topic": memory.get("topic"),
                    "content": memory.get("content"),
                    "status": memory.get("status"),
                },
            })

        # Sort by confidence and relation priority
        relation_priority = {
            "duplicate": 0,
            "conflict": 1,
            "update": 2,
            "new": 3,
        }
        similar_decisions.sort(
            key=lambda d: (
                relation_priority.get(d["relation"], 4),
                -d["confidence"]
            )
        )

        return {
            "similar_decisions": similar_decisions[:top_k],
            "total_found": len(similar_decisions),
        }

    def _classify_decision_relation(
        self,
        query: str,
        text: Optional[str],
        existing_memory: Dict[str, Any],
    ) -> tuple:
        """Classify the relationship between a new query and existing memory.

        Args:
            query: New query text.
            text: Optional full text for comparison.
            existing_memory: Existing memory dict.

        Returns:
            Tuple of (relation_type, reason, confidence).
            relation_type: "duplicate", "update", "conflict", or "new"
        """
        # Get existing memory content
        existing_title = existing_memory.get("title", "").lower()
        existing_content = existing_memory.get("content", "").lower()
        existing_topic = existing_memory.get("topic", "").lower()

        # Normalize query
        query_lower = query.lower()
        text_lower = text.lower() if text else ""

        # Combine query and text for comparison
        new_text = query_lower + " " + text_lower
        existing_text = existing_title + " " + existing_content + " " + existing_topic

        # Calculate overlap metrics
        query_keywords = set(self._extract_keywords(query))
        text_keywords = set(self._extract_keywords(text_lower)) if text else set()
        existing_keywords = set(self._extract_keywords(existing_text))

        # Keyword overlap ratio
        all_keywords = query_keywords | text_keywords | existing_keywords
        if not all_keywords:
            return "new", "No keywords for comparison", 0.0

        # Calculate different overlap ratios
        query_overlap = len(query_keywords & existing_keywords) / max(len(query_keywords), 1)
        text_overlap = len(text_keywords & existing_keywords) / max(len(text_keywords), 1) if text_keywords else 0
        total_overlap = len((query_keywords | text_keywords) & existing_keywords) / len(all_keywords)

        # Text similarity (simple character overlap for Chinese)
        text_similarity = self._calculate_text_similarity(new_text, existing_text)

        new_positive, new_negative, new_objects = self._decision_polarity(new_text)
        old_positive, old_negative, old_objects = self._decision_polarity(existing_text)
        shared_objects = new_objects & old_objects
        if shared_objects and ((new_positive and old_negative) or (new_negative and old_positive)):
            return (
                "conflict",
                "检测到冲突：新决策与现有决策对同一对象表达了相反取向",
                0.75,
            )

        # Decision logic
        status = existing_memory.get("status", "active")

        # High overlap = potential duplicate
        if query_overlap > 0.6 or text_similarity > 0.7:
            confidence = max(query_overlap, text_similarity)
            return "duplicate", f"高度相似：关键词重叠 {query_overlap:.0%}，文本相似 {text_similarity:.0%}", confidence

        # Medium overlap with more content = potential update
        if (query_overlap > 0.3 or text_similarity > 0.4) and len(text_lower) > len(existing_content) * 0.8:
            confidence = (query_overlap + text_similarity) / 2
            return "update", f"可能更新：与现有决策相关且有更多细节", confidence

        # Low overlap but same topic = related decision
        if query_overlap > 0.1 or total_overlap > 0.15:
            confidence = max(query_overlap, total_overlap)
            return "update", f"相关决策：主题相近但内容不同", confidence

        # No significant overlap = new decision
        return "new", "新决策：与现有决策无明显关联", 0.0

    def _decision_polarity(self, text: str) -> tuple:
        """Infer simple positive/negative decision polarity and mentioned objects."""
        lower_text = text.lower()
        positive_markers = ["使用", "采用", "选择", "保留", "继续", "use", "adopt", "choose"]
        negative_markers = [
            "不使用",
            "不用",
            "取消",
            "放弃",
            "弃用",
            "不采用",
            "not use",
            "do not use",
            "drop",
            "abandon",
            "deprecate",
        ]

        has_negative = any(marker in lower_text for marker in negative_markers)
        has_positive = any(marker in lower_text for marker in positive_markers)

        words = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}", text))
        chinese_terms = set(re.findall(r"[\u4e00-\u9fa5]{2,}", text))
        objects = words | {
            term
            for term in chinese_terms
            if term not in self.CHINESE_STOP_WORDS and len(term) <= 12
        }
        return has_positive, has_negative, objects

    def _calculate_text_similarity(self, text1: str, text2: str) -> float:
        """Calculate simple text similarity based on character/bigram overlap.

        Args:
            text1: First text.
            text2: Second text.

        Returns:
            Similarity ratio 0.0-1.0.
        """
        if not text1 or not text2:
            return 0.0

        # For Chinese, use bigrams
        chars1 = [c for c in text1 if "\u4e00" <= c <= "\u9fa5"]
        chars2 = [c for c in text2 if "\u4e00" <= c <= "\u9fa5"]

        english_words1 = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}", text1.lower()))
        english_words2 = set(re.findall(r"[a-zA-Z][a-zA-Z0-9_+.-]{1,}", text2.lower()))
        english_similarity = 0.0
        if english_words1 and english_words2:
            english_similarity = len(english_words1 & english_words2) / len(english_words1 | english_words2)

        if chars1 and chars2:
            bigrams1 = {chars1[i] + chars1[i+1] for i in range(len(chars1) - 1)}
            bigrams2 = {chars2[i] + chars2[i+1] for i in range(len(chars2) - 1)}

            if not bigrams1 or not bigrams2:
                return english_similarity

            intersection = bigrams1 & bigrams2
            union = bigrams1 | bigrams2
            chinese_similarity = len(intersection) / len(union)
            return max(chinese_similarity, english_similarity)

        # For English, use word overlap
        words1 = english_words1 or set(text1.lower().split())
        words2 = english_words2 or set(text2.lower().split())

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union)

    def get_memory_by_id(
        self,
        memory_id: str,
        include_evidence: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific memory by ID.

        Args:
            memory_id: Memory ID.
            include_evidence: Whether to include source evidence.

        Returns:
            Memory dict or None if not found.
        """
        memory = self.memory_store.get_memory(memory_id)

        if memory and include_evidence:
            memory["evidence"] = self._format_evidence(memory.get("sources", []))

        return memory

    def get_memories_by_topic(
        self,
        project_id: str,
        topic: str,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """Get memories by topic.

        Args:
            project_id: Project ID.
            topic: Topic to search for.
            status: Status filter.

        Returns:
            List of memory dicts.
        """
        return self.memory_store.get_memory_by_topic(
            project_id=project_id,
            topic=topic,
            status=status,
        )

    def get_active_decisions(
        self,
        project_id: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get all active decisions for a project.

        Args:
            project_id: Project ID.
            limit: Maximum results.

        Returns:
            List of decision memory dicts.
        """
        return self.memory_store.search_memories(
            project_id=project_id,
            memory_types=["decision"],
            status="active",
            time_scope="current",
            limit=limit,
        )

    def get_decision_history(
        self,
        project_id: str,
        topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get decision history including superseded versions.

        Args:
            project_id: Project ID.
            topic: Optional topic filter.

        Returns:
            List of decision dicts including superseded.
        """
        if topic:
            memories = self.memory_store.get_memory_by_topic(
                project_id=project_id,
                topic=topic,
                status="all",
            )
        else:
            memories = self.memory_store.search_memories(
                project_id=project_id,
                memory_types=["decision"],
                status="all",
                time_scope="all",
                limit=100,
            )

        # Include version chain info
        for memory in memories:
            supersedes = memory.get("supersedes")
            if supersedes:
                old_memory = self.memory_store.get_memory(supersedes)
                if old_memory:
                    memory["supersedes_memory"] = old_memory

        return memories

    def _build_answer(
        self,
        query: str,
        memories: List[Dict[str, Any]],
    ) -> str:
        """Build an answer from retrieved memories.

        Args:
            query: Original query.
            memories: Retrieved memory dicts.

        Returns:
            Answer string.
        """
        if not memories:
            # Try to detect language from query
            # Use simple heuristic: if contains Chinese characters, use Chinese response
            if any(ord(c) > 127 for c in query):
                return f"未找到与「{query}」相关的记忆。"
            return f"No memories found related to '{query}'."

        # Group by topic
        by_topic: Dict[str, List[Dict[str, Any]]] = {}
        for memory in memories:
            topic = memory.get("topic", "Other")
            if topic not in by_topic:
                by_topic[topic] = []
            by_topic[topic].append(memory)

        # Build answer
        parts = []
        for topic, topic_memories in by_topic.items():
            parts.append(f"## {topic}")

            for memory in topic_memories:
                status_emoji = {
                    "active": "✓",
                    "superseded": "⊘",
                    "pending": "?",
                }.get(memory.get("status", ""), "•")

                title = memory.get("title", memory.get("topic", ""))
                content = memory.get("content", "")

                parts.append(f"{status_emoji} **{title}**")
                if content:
                    parts.append(f"  {content}")

        return "\n".join(parts)

    def _format_evidence(
        self,
        sources: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Format source evidence.

        Args:
            sources: List of source dicts.

        Returns:
            Formatted evidence list.
        """
        evidence = []

        for source in sources:
            evidence.append({
                "event_id": source.get("event_id", ""),
                "quote": source.get("quote", ""),
                "content": source.get("content", "")[:100],
                "timestamp": source.get("timestamp", ""),
            })

        return evidence


class VectorStorePlaceholder:
    """Placeholder for future vector store integration.

    This class provides a placeholder interface for vector-based retrieval
    using stores like Chroma or FAISS.
    """

    def __init__(self, db_path: Optional[str] = None):
        """Initialize placeholder vector store.

        Args:
            db_path: Path to vector store database.
        """
        self._initialized = False

    def add_memories(self, memories: List[Dict[str, Any]]) -> bool:
        """Add memories to vector store.

        Args:
            memories: List of memory dicts.

        Returns:
            True if successful.
        """
        # TODO: Implement vector store integration
        return True

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search by vector similarity.

        Args:
            query: Query text.
            top_k: Maximum results.
            filters: Optional metadata filters.

        Returns:
            List of memory dicts with similarity scores.
        """
        # TODO: Implement vector search
        return []

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from vector store.

        Args:
            memory_id: Memory ID to delete.

        Returns:
            True if successful.
        """
        # TODO: Implement deletion
        return True
