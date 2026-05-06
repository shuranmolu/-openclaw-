"""
Heuristic consolidation proposals for project memories and candidates.

The consolidator does not mutate memory state by itself. It returns explainable
duplicate/update/conflict/support proposals so OpenClaw can decide what to do.
"""

import re
from itertools import combinations
from typing import Any, Dict, List

from .topic_tracker import tokenize_text, token_overlap_ratio


NEGATION_RE = re.compile(
    r"\u4e0d|\u4e0d\u8981|\u4e0d\u7528|\u53d6\u6d88|\u5e9f\u5f03|\u53cd\u5bf9|"
    r"instead|replace|cancel|not use|no longer|conflict",
    re.IGNORECASE,
)
UPDATE_RE = re.compile(
    r"\u6539\u6210|\u6539\u4e3a|\u6362\u6210|\u66f4\u65b0|\u91cd\u65b0|"
    r"instead|replace|change to|update",
    re.IGNORECASE,
)


class MemoryConsolidator:
    """Build pairwise consolidation proposals."""

    def propose(
        self,
        items: List[Dict[str, Any]],
        min_overlap: float = 0.25,
        max_proposals: int = 50,
    ) -> Dict[str, Any]:
        proposals: List[Dict[str, Any]] = []
        for left, right in combinations(items, 2):
            relation = self.classify_pair(left, right, min_overlap=min_overlap)
            if relation["relation"] != "unrelated":
                proposals.append(relation)

        summary = {
            "duplicate": sum(1 for item in proposals if item["relation"] == "duplicate"),
            "update": sum(1 for item in proposals if item["relation"] == "update"),
            "conflict": sum(1 for item in proposals if item["relation"] == "conflict"),
            "support": sum(1 for item in proposals if item["relation"] == "support"),
        }
        relation_priority = {"conflict": 0, "update": 1, "duplicate": 2, "support": 3}
        proposals.sort(
            key=lambda item: (
                relation_priority.get(item["relation"], 9),
                -float(item.get("confidence", 0.0)),
                item.get("left_id", ""),
                item.get("right_id", ""),
            )
        )
        max_proposals = max(0, int(max_proposals))
        return {
            "proposal_count": len(proposals),
            "returned_count": min(len(proposals), max_proposals),
            "summary": summary,
            "proposals": proposals[:max_proposals],
        }

    def classify_pair(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        min_overlap: float = 0.25,
    ) -> Dict[str, Any]:
        """Classify the relation between two memory-like dictionaries."""
        left_text = self._text(left)
        right_text = self._text(right)
        overlap = token_overlap_ratio(tokenize_text(left_text), tokenize_text(right_text))
        shared_evidence = bool(set(self._evidence(left)) & set(self._evidence(right)))
        same_topic = self._topic(left) == self._topic(right)
        same_type = self._type(left) == self._type(right)

        if not same_topic and overlap < 0.45 and not shared_evidence:
            return self._result(left, right, "unrelated", overlap, "different_topic_low_overlap")

        left_neg = bool(NEGATION_RE.search(left_text))
        right_neg = bool(NEGATION_RE.search(right_text))
        has_update = bool(UPDATE_RE.search(left_text) or UPDATE_RE.search(right_text))

        if same_topic and (left_neg != right_neg or has_update) and overlap >= 0.15:
            relation = "update" if has_update else "conflict"
            return self._result(left, right, relation, max(overlap, 0.55), "topic_match_with_negation_or_update")

        if (same_topic and same_type and overlap >= 0.65) or shared_evidence:
            return self._result(left, right, "duplicate", max(overlap, 0.7), "same_topic_type_high_overlap_or_shared_evidence")

        if same_topic and overlap >= min_overlap:
            return self._result(left, right, "support", overlap, "same_topic_context_support")

        return self._result(left, right, "unrelated", overlap, "no_merge_signal")

    def _result(
        self,
        left: Dict[str, Any],
        right: Dict[str, Any],
        relation: str,
        confidence: float,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "left_id": self._id(left),
            "right_id": self._id(right),
            "relation": relation,
            "confidence": round(float(confidence), 3),
            "reason": reason,
            "left_topic": self._topic(left),
            "right_topic": self._topic(right),
            "suggested_action": self._suggested_action(relation),
        }

    def _suggested_action(self, relation: str) -> str:
        return {
            "duplicate": "merge_or_ignore_duplicate",
            "update": "ask_openclaw_to_choose_upsert_or_supersede",
            "conflict": "require_review_before_push",
            "support": "attach_as_rationale_or_evidence",
        }.get(relation, "keep_separate")

    def _id(self, item: Dict[str, Any]) -> str:
        return str(item.get("memory_id") or item.get("candidate_id") or item.get("id") or "")

    def _topic(self, item: Dict[str, Any]) -> str:
        return str(item.get("topic_normalized") or item.get("topic") or "general").lower()

    def _type(self, item: Dict[str, Any]) -> str:
        return str(item.get("memory_type") or item.get("candidate_type") or "unknown").lower()

    def _text(self, item: Dict[str, Any]) -> str:
        return " ".join([
            str(item.get("title") or ""),
            str(item.get("summary") or ""),
            str(item.get("content") or ""),
        ]).lower()

    def _evidence(self, item: Dict[str, Any]) -> List[str]:
        return list(item.get("evidence_event_ids") or item.get("source_event_ids") or [])
