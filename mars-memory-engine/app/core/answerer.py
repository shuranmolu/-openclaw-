"""
Compose user-facing answers from retrieved memories.
"""

from typing import Any, Dict, List

from .query_planner import QueryPlan


class MemoryAnswerer:
    """Deterministic answer composer with evidence passthrough."""

    def compose(self, plan: QueryPlan, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not memories:
            return {
                "answer": "I could not find a strong active memory for that query yet.",
                "primary_memory_id": None,
                "evidence": [],
            }

        primary = self._select_primary(plan, memories)
        supporting = [
            memory for memory in memories
            if memory.get("memory_id") != primary.get("memory_id")
        ]

        answer = self._render(plan, primary, supporting)
        return {
            "answer": answer,
            "primary_memory_id": primary.get("memory_id"),
            "evidence": self._evidence(memories),
        }

    def _select_primary(self, plan: QueryPlan, memories: List[Dict[str, Any]]) -> Dict[str, Any]:
        def score(memory: Dict[str, Any]) -> tuple:
            type_score = 1 if memory.get("memory_type") in plan.primary_types else 0
            topic_score = 1 if plan.normalized_topic and self._topic_matches(memory, plan.normalized_topic) else 0
            return (
                type_score + topic_score,
                float(memory.get("score", 0) or 0),
                float(memory.get("importance", 0) or 0),
            )

        return max(memories, key=score)

    def _render(
        self,
        plan: QueryPlan,
        primary: Dict[str, Any],
        supporting: List[Dict[str, Any]],
    ) -> str:
        content = primary.get("content") or primary.get("summary") or primary.get("title") or ""
        rationale = primary.get("rationale") or primary.get("rationale_json") or []
        if not isinstance(rationale, list):
            rationale = []

        if plan.query_type == "risk_lookup":
            prefix = "Current risk memory"
        elif plan.query_type == "timeline_lookup":
            prefix = "Timeline memory"
        elif plan.query_type == "explanation":
            prefix = "Relevant decision basis"
        elif plan.query_type == "action_lookup":
            prefix = "Relevant action memory"
        else:
            prefix = "Relevant memory"

        parts = [f"{prefix}: {content}"]
        if rationale:
            parts.append(f"Basis: {'; '.join(str(item) for item in rationale[:3])}.")
        if supporting:
            titles = [
                str(item.get("title") or item.get("content") or item.get("memory_id"))
                for item in supporting[:3]
            ]
            parts.append(f"Related memories: {', '.join(titles)}.")
        return " ".join(parts)

    def _topic_matches(self, memory: Dict[str, Any], topic: str) -> bool:
        raw = " ".join([
            str(memory.get("topic") or ""),
            str(memory.get("title") or ""),
            str(memory.get("content") or ""),
        ]).lower()
        return topic.lower() in raw

    def _evidence(self, memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        evidence = []
        for memory in memories:
            evidence.append({
                "memory_id": memory.get("memory_id"),
                "memory_type": memory.get("memory_type"),
                "topic": memory.get("topic"),
                "title": memory.get("title"),
                "score": memory.get("score"),
                "sources": memory.get("sources") or memory.get("evidence") or [],
            })
        return evidence
