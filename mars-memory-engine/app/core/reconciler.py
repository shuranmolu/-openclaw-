"""
Memory Reconciler for MARS Memory Engine.

Detects conflicts and manages supersede relationships between memories.
"""

import re
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..storage.memory_store import MemoryStore


class MemoryReconciler:
    """Reconciles new memories with existing ones to detect conflicts."""

    # Supersede keywords - extended to capture more variations
    SUPERSEDE_PATTERNS = [
        r"改成|换成|改为|变更|更新|推翻|重新",
        r"不用.*了|不再.*|取消|作废|放弃",
        r"新方案|新的|重做",
    ]

    # Enhanced supersede patterns for detecting changes in active memories
    ACTIVE_SUPERSEDE_PATTERNS = [
        r"改成|改为|换成|改用|采用|使用",  # Change to X
        r"作废|不用|不再|取消|放弃|推翻",  # Invalidate/abandon X
        r"重新|重做|新的|更新",  # Redo/new
    ]

    # Conflict keywords
    CONFLICT_PATTERNS = [
        r"不对|不是|错误|有问题",
        r"等一下|等等|但是",
        r"我反对|不同意|不应该",
    ]

    def __init__(self, memory_store: MemoryStore):
        """Initialize reconciler.

        Args:
            memory_store: MemoryStore instance.
        """
        self.memory_store = memory_store
        self.supersede_regex = re.compile("|".join(self.SUPERSEDE_PATTERNS), re.IGNORECASE)
        self.active_supersede_regex = re.compile("|".join(self.ACTIVE_SUPERSEDE_PATTERNS), re.IGNORECASE)
        self.conflict_regex = re.compile("|".join(self.CONFLICT_PATTERNS), re.IGNORECASE)

    def reconcile_statement(
        self,
        project_id: str,
        new_statement: str,
        context_event_ids: Optional[List[str]] = None,
        auto_resolve: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile a new statement with existing memories.

        Args:
            project_id: Project ID.
            new_statement: New statement content.
            context_event_ids: Optional related event IDs.
            auto_resolve: Whether to auto-resolve high-confidence conflicts.

        Returns:
            Dict with relation, old_memory_id, new_memory_id, etc.
        """
        # Search for related active memories
        related = self._find_related_memories(project_id, new_statement)

        if not related:
            return {
                "relation": "new",
                "old_memory_id": None,
                "new_memory_id": None,
                "reason": "No related memories found",
                "action_taken": "No action needed",
                "requires_human_review": False,
            }

        # Check for supersede relationship
        if self.supersede_regex.search(new_statement):
            best_match = related[0]
            old_memory_id = best_match["memory_id"]

            if auto_resolve:
                # Auto-resolve: create new memory and mark old as superseded
                # This would be called after the new memory is created
                return {
                    "relation": "supersede",
                    "old_memory_id": old_memory_id,
                    "new_memory_id": None,  # To be filled by caller
                    "reason": f"New statement contains supersede keywords and matches existing memory on topic: {best_match.get('topic')}",
                    "action_taken": "Ready to mark old memory as superseded",
                    "requires_human_review": False,
                }
            else:
                return {
                    "relation": "supersede",
                    "old_memory_id": old_memory_id,
                    "new_memory_id": None,
                    "reason": f"New statement contains supersede keywords",
                    "action_taken": "Requires confirmation",
                    "requires_human_review": True,
                }

        # Check for conflict
        if self.conflict_regex.search(new_statement):
            return {
                "relation": "conflict",
                "old_memory_id": related[0]["memory_id"],
                "new_memory_id": None,
                "reason": "New statement indicates potential conflict",
                "action_taken": "Flagged for human review",
                "requires_human_review": True,
            }

        # Check for support/relationship
        if self._is_supportive(new_statement, related[0]):
            return {
                "relation": "support",
                "old_memory_id": related[0]["memory_id"],
                "new_memory_id": None,
                "reason": "New statement supports existing memory",
                "action_taken": "No action needed",
                "requires_human_review": False,
            }

        # Default: treat as update candidate
        return {
            "relation": "update",
            "old_memory_id": related[0]["memory_id"],
            "new_memory_id": None,
            "reason": "Related memory exists",
            "action_taken": "May be an update to existing memory",
            "requires_human_review": True,
        }

    def apply_supersede(
        self,
        old_memory_id: str,
        new_memory_id: str,
    ) -> bool:
        """Apply supersede relationship between memories.

        Args:
            old_memory_id: Old memory ID to be superseded.
            new_memory_id: New memory ID that supersedes the old one.

        Returns:
            True if successful.
        """
        # Update old memory
        self.memory_store.update_memory_status(
            old_memory_id,
            status="superseded",
            superseded_by=new_memory_id,
        )

        # Update new memory
        self.memory_store.update_memory_status(
            new_memory_id,
            status="active",
            supersedes=old_memory_id,
        )

        # Create edge
        self.memory_store.create_edge(
            source_memory_id=new_memory_id,
            target_memory_id=old_memory_id,
            relation_type="supersede",
            reason="Auto-detected supersede relationship",
            confidence=0.8,
        )

        return True

    def reconcile_project(
        self,
        project_id: str,
    ) -> Dict[str, Any]:
        """Reconcile all memories in a project.

        Detects conflicts and supersede relationships among active memories.

        Args:
            project_id: Project ID.

        Returns:
            Dict with reconciliation results.
        """
        # Get all active memories
        active_memories = self.memory_store.get_active_memories(project_id)

        # Get all pending candidates
        candidates = self.memory_store.get_candidates(project_id)

        results = {
            "supersede_found": 0,
            "conflicts_found": 0,
            "processed": 0,
        }

        # Check candidates against existing memories
        for candidate in candidates:
            # Find related memories by topic
            related = self.memory_store.get_memory_by_topic(
                project_id,
                candidate["topic"],
                status="active",
            )

            if not related:
                continue

            for existing in related:
                # Check if candidate content indicates supersede
                if self.supersede_regex.search(candidate["summary"]):
                    results["supersede_found"] += 1

                    # Create an edge to record this
                    self.memory_store.create_edge(
                        source_memory_id=f"candidate_{candidate['candidate_id']}",
                        target_memory_id=existing["memory_id"],
                        relation_type="supersede",
                        reason="Candidate indicates supersede relationship",
                    )

                # Check for conflicts
                elif self._is_conflicting(candidate["summary"], existing):
                    results["conflicts_found"] += 1

                    self.memory_store.create_edge(
                        source_memory_id=f"candidate_{candidate['candidate_id']}",
                        target_memory_id=existing["memory_id"],
                        relation_type="conflict",
                        reason="Content conflicts with existing memory",
                    )

            results["processed"] += 1

        return results

    def _find_related_memories(
        self,
        project_id: str,
        statement: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Find memories related to a statement.

        Args:
            project_id: Project ID.
            statement: Statement text.
            limit: Maximum results.

        Returns:
            List of related memory dicts.
        """
        # Use keyword-based search first
        results = self.memory_store.search_memories(
            project_id=project_id,
            query=statement[:50],
            status="active",
            time_scope="current",
            limit=limit,
        )

        # If no results and statement contains supersede keywords,
        # return all active memories for broader matching.
        # This handles brief statements like "改成 Vue 吧" where the topic
        # is implied from context rather than explicitly stated.
        if not results and self.supersede_regex.search(statement):
            results = self.memory_store.search_memories(
                project_id=project_id,
                status="active",
                time_scope="current",
                limit=limit,
            )

        return results

    def _is_supportive(self, statement: str, memory: Dict[str, Any]) -> bool:
        """Check if a statement supports an existing memory.

        Args:
            statement: New statement.
            memory: Existing memory dict.

        Returns:
            True if supportive.
        """
        # Simple check: if statement adds to memory without contradicting
        memory_content = memory.get("content", "").lower()

        # Check for agreement words
        agreement_words = ["同意", "对", "是的", "好的", "没问题", "可以"]
        if any(word in statement.lower() for word in agreement_words):
            return True

        return False

    def _is_conflicting(self, candidate: str, memory: Dict[str, Any]) -> bool:
        """Check if a candidate conflicts with a memory.

        Args:
            candidate: Candidate summary.
            memory: Existing memory dict.

        Returns:
            True if conflicting.
        """
        # Check for explicit conflict keywords
        if self.conflict_regex.search(candidate):
            return True

        # Check for negation patterns
        memory_content = memory.get("content", "").lower()
        candidate_lower = candidate.lower()

        # If candidate says "not X" but memory says "X", that's a conflict
        if "不" in candidate_lower or "没" in candidate_lower or "别" in candidate_lower:
            # Check if the negated term appears in memory
            negated = candidate_lower.replace("不", "").replace("没", "").replace("别", "")
            if negated.strip() and negated.strip() in memory_content:
                return True

        return False


def auto_reconcile_updates(
    project_id: str,
    memory_store: MemoryStore,
) -> List[Dict[str, Any]]:
    """Auto-reconcile memory updates for a project.

    Finds candidates that indicate supersede relationships and applies them.
    Also scans active decision memories to detect supersede relationships.

    Args:
        project_id: Project ID.
        memory_store: MemoryStore instance.

    Returns:
        List of applied supersede relationships.
    """
    reconciler = MemoryReconciler(memory_store)
    results = []

    # Part 1: Process pending candidates (original logic)
    candidates = memory_store.get_candidates(project_id)

    for candidate in candidates:
        # Only process high-confidence decision candidates
        if candidate["candidate_type"] != "decision":
            continue

        if candidate["confidence"] < 0.7:
            continue

        # Check for supersede keywords
        summary = candidate.get("summary", "")
        if reconciler.supersede_regex.search(summary):
            # Find related memories by topic
            related = memory_store.get_memory_by_topic(
                project_id,
                candidate["topic"],
                status="active",
            )

            if related:
                # Commit candidate
                mem_result = memory_store.commit_candidate(
                    candidate["candidate_id"],
                    status="active",
                )

                if mem_result:
                    new_memory_id = mem_result["memory_id"]
                    old_memory_id = related[0]["memory_id"]

                    # Apply supersede
                    reconciler.apply_supersede(old_memory_id, new_memory_id)

                    results.append({
                        "old_memory_id": old_memory_id,
                        "new_memory_id": new_memory_id,
                        "topic": candidate["topic"],
                    })

    # Part 2: Scan active decision memories for supersede relationships
    # This handles cases where candidates were already committed (e.g., auto_commit=True)
    from collections import defaultdict

    active_memories = memory_store.get_active_memories(project_id, memory_type="decision")

    # Filter to only decision memories that haven't been processed yet
    # (no supersedes relationship already set)
    decision_memories = [m for m in active_memories if m.get("supersedes") is None]

    # Group by topic
    topic_memories = defaultdict(list)
    for memory in decision_memories:
        topic = memory.get("topic", "unknown")
        topic_memories[topic].append(memory)

    # Check each topic group for supersede relationships
    for topic, memories in topic_memories.items():
        if len(memories) < 2:
            continue

        # Sort by valid_time_start or created_at (oldest first)
        def get_time(mem):
            time_str = mem.get("valid_time_start") or mem.get("created_at") or ""
            try:
                return datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return datetime.min

        sorted_memories = sorted(memories, key=get_time)

        # Check for supersede patterns
        for i in range(len(sorted_memories)):
            for j in range(i + 1, len(sorted_memories)):
                older = sorted_memories[i]
                newer = sorted_memories[j]

                # Skip if either already has supersede relationships
                if older.get("status") != "active" or newer.get("status") != "active":
                    continue
                if older.get("superseded_by") is not None or newer.get("supersedes") is not None:
                    continue

                # Check if newer memory contains supersede keywords
                newer_content = newer.get("content", "").lower()
                newer_title = newer.get("title", "").lower()

                if reconciler.active_supersede_regex.search(newer_content) or \
                   reconciler.active_supersede_regex.search(newer_title):

                    # Check if there's thematic overlap (simple keyword matching)
                    older_content = older.get("content", "").lower()
                    older_title = older.get("title", "").lower()

                    # Extract potential solution/technology terms
                    # Common tech terms that might be changed
                    tech_terms = []

                    # Look for terms in older memory that might be superseded
                    for text in [older_title, older_content]:
                        # Extract potential tech terms (capitalized words, common patterns)
                        words = re.findall(r'\b[A-Z][a-z]+\b|\b(?:Streamlit|Vue|FastAPI|React|Flask|Django|Angular|Node\.js)\b', text)
                        tech_terms.extend(words)

                    # Check if any terms from older memory appear in newer memory
                    has_overlap = False
                    for term in tech_terms:
                        if term.lower() in newer_content or term.lower() in newer_title:
                            has_overlap = True
                            break

                    # Also check topic overlap (most reliable)
                    if older.get("topic") == newer.get("topic"):
                        has_overlap = True

                    if has_overlap:
                        # Apply supersede relationship
                        old_memory_id = older["memory_id"]
                        new_memory_id = newer["memory_id"]

                        reconciler.apply_supersede(old_memory_id, new_memory_id)

                        results.append({
                            "old_memory_id": old_memory_id,
                            "new_memory_id": new_memory_id,
                            "topic": topic,
                        })

                        # Only apply one supersede per older memory
                        break

    return results
