"""
Lightweight query planning for memory retrieval.

The planner converts a user query into a small retrieval plan so search can
prefer the right memory types instead of treating every query the same way.
"""

from dataclasses import asdict, dataclass
import re
from typing import List, Optional


TOKEN_RE = re.compile(r"[a-zA-Z0-9_#+.-]+|[\u4e00-\u9fff]{2,}")


@dataclass
class QueryPlan:
    query: str
    normalized_topic: Optional[str]
    query_type: str
    top_k: int
    preferred_types: List[str]
    primary_types: List[str]
    strict_topic: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class QueryPlanner:
    """Heuristic-first query planner."""

    TOPIC_MARKERS = {
        "architecture": [
            "architecture", "framework", "stack", "backend", "frontend",
            "plugin", "openclaw", "mars", "engine", "api",
            "\u67b6\u6784", "\u6846\u67b6", "\u63d2\u4ef6", "\u6280\u672f",
            "\u65b9\u6848", "\u63a5\u53e3",
        ],
        "timeline": [
            "deadline", "schedule", "milestone", "when", "date", "time",
            "\u622a\u6b62", "\u6392\u671f", "\u65f6\u95f4", "\u65e5\u671f",
            "\u4e0a\u7ebf", "\u53d1\u5e03",
        ],
        "risk": [
            "risk", "issue", "problem", "blocker", "concern", "conflict",
            "\u98ce\u9669", "\u95ee\u9898", "\u51b2\u7a81", "\u53cd\u5bf9",
            "\u963b\u585e",
        ],
        "ownership": [
            "owner", "assign", "ownership",
            "\u8d1f\u8d23", "\u8d1f\u8d23\u4eba", "\u5206\u5de5",
        ],
        "governance": [
            "card", "bitable", "audit", "evidence", "governance",
            "\u5361\u7247", "\u591a\u7ef4\u8868\u683c", "\u8bc1\u636e",
            "\u6cbb\u7406",
        ],
    }

    def plan(self, query: str, top_k: int = 5) -> QueryPlan:
        lowered = (query or "").lower()
        normalized_topic = self._infer_topic(lowered)

        query_type = "general"
        preferred_types = ["decision", "fact"]
        primary_types = ["decision"]
        strict_topic = False

        if self._contains(lowered, ["why", "reason", "\u4e3a\u4ec0\u4e48", "\u539f\u56e0"]):
            query_type = "explanation"
            preferred_types = ["decision", "fact", "risk"]
            primary_types = ["decision", "risk"]
        elif self._contains(lowered, ["risk", "issue", "problem", "conflict", "\u98ce\u9669", "\u95ee\u9898", "\u51b2\u7a81"]):
            query_type = "risk_lookup"
            normalized_topic = normalized_topic or "risk"
            preferred_types = ["risk", "decision", "procedure", "fact"]
            primary_types = ["risk", "decision"]
            strict_topic = True
        elif self._contains(lowered, ["when", "deadline", "timeline", "schedule", "\u4ec0\u4e48\u65f6\u5019", "\u622a\u6b62", "\u65f6\u95f4", "\u6392\u671f"]):
            query_type = "timeline_lookup"
            normalized_topic = normalized_topic or "timeline"
            preferred_types = ["fact", "decision", "risk"]
            primary_types = ["fact", "decision"]
        elif self._contains(lowered, ["current", "latest", "\u5f53\u524d", "\u6700\u65b0", "\u73b0\u5728"]):
            query_type = "current_state"
            preferred_types = ["decision", "fact", "procedure"]
            primary_types = ["decision"]
        elif self._contains(lowered, ["onboarding", "handoff", "context", "\u4ea4\u63a5", "\u80cc\u666f", "\u4e0a\u4e0b\u6587"]):
            query_type = "onboarding_lookup"
            preferred_types = ["decision", "procedure", "fact"]
            primary_types = ["decision", "procedure"]
        elif self._contains(lowered, ["action", "todo", "\u5f85\u529e", "\u884c\u52a8\u9879"]):
            query_type = "action_lookup"
            preferred_types = ["procedure", "decision", "fact"]
            primary_types = ["procedure", "decision"]

        return QueryPlan(
            query=query,
            normalized_topic=normalized_topic,
            query_type=query_type,
            top_k=top_k,
            preferred_types=preferred_types,
            primary_types=primary_types,
            strict_topic=strict_topic,
        )

    def _infer_topic(self, lowered_query: str) -> Optional[str]:
        tokens = set(TOKEN_RE.findall(lowered_query))
        for topic, markers in self.TOPIC_MARKERS.items():
            for marker in markers:
                marker_lower = marker.lower()
                if marker_lower in lowered_query or marker_lower in tokens:
                    return topic
        return None

    def _contains(self, text: str, markers: List[str]) -> bool:
        return any(marker.lower() in text for marker in markers)
