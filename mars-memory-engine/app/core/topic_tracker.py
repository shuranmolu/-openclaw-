"""
Lightweight topic tracking for raw chat/doc events.

The tracker is deterministic on purpose. It gives the window builder enough
signal to split mixed Feishu discussions without introducing another model call.
"""

import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Set


TOKEN_RE = re.compile(r"[a-zA-Z0-9_#+.-]+|[\u4e00-\u9fff]{2,}")


DEFAULT_TOPIC_MARKERS = {
    "architecture": [
        "architecture", "framework", "stack", "backend", "frontend", "plugin",
        "openclaw", "mars", "memory", "engine",
        "\u67b6\u6784", "\u6846\u67b6", "\u63d2\u4ef6", "\u8bb0\u5fc6",
        "\u670d\u52a1", "\u6a21\u5757",
    ],
    "timeline": [
        "deadline", "schedule", "milestone", "release", "date", "time",
        "\u622a\u6b62", "\u6392\u671f", "\u65e5\u671f", "\u4e0a\u7ebf",
        "\u53d1\u5e03", "\u5468\u671f", "\u9636\u6bb5",
    ],
    "risk": [
        "risk", "issue", "problem", "blocker", "concern", "conflict",
        "\u98ce\u9669", "\u95ee\u9898", "\u963b\u585e", "\u62c5\u5fc3",
        "\u51b2\u7a81", "\u53cd\u5bf9",
    ],
    "ownership": [
        "owner", "assign", "ownership",
        "\u8d1f\u8d23", "\u8d1f\u8d23\u4eba", "\u5206\u5de5",
        "\u8c01\u6765",
    ],
    "governance": [
        "bitable", "table", "record", "card", "audit", "governance",
        "\u591a\u7ef4\u8868\u683c", "\u5361\u7247", "\u6cbb\u7406",
        "\u5ba1\u8ba1", "\u8bc1\u636e\u94fe",
    ],
    "product": [
        "user", "feature", "experience", "requirement", "workflow",
        "\u7528\u6237", "\u529f\u80fd", "\u4f53\u9a8c", "\u9700\u6c42",
        "\u6d41\u7a0b",
    ],
}


def tokenize_text(text: str) -> Set[str]:
    """Return normalized tokens for cheap lexical overlap."""
    if not text:
        return set()
    return {token.lower() for token in TOKEN_RE.findall(text)}


def token_overlap_ratio(left: Iterable[str], right: Iterable[str]) -> float:
    """Measure overlap against the smaller token set."""
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / min(len(left_set), len(right_set))


class TopicTracker:
    """Assign coarse topic labels to events and windows."""

    def __init__(
        self,
        topic_markers: Dict[str, List[str]] | None = None,
        reuse_threshold: float = 0.35,
    ):
        self.topic_markers = topic_markers or DEFAULT_TOPIC_MARKERS
        self.reuse_threshold = reuse_threshold

    def annotate_event(
        self,
        event: Dict[str, Any],
        previous: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Return a topic annotation for one raw event."""
        content = event.get("content", "") or ""
        tokens = tokenize_text(content)
        marker_scores = self._score_markers(content.lower(), tokens)

        if marker_scores:
            topic, markers = marker_scores[0]
            confidence = min(0.95, 0.55 + 0.12 * len(markers))
            return {
                "event_id": event.get("event_id"),
                "topic_label": topic,
                "confidence": confidence,
                "reason": "marker_match",
                "matched_markers": markers,
                "tokens": sorted(tokens),
            }

        if previous:
            previous_tokens = set(previous.get("tokens") or [])
            overlap = token_overlap_ratio(tokens, previous_tokens)
            if overlap >= self.reuse_threshold:
                return {
                    "event_id": event.get("event_id"),
                    "topic_label": previous.get("topic_label", "general"),
                    "confidence": min(0.8, max(0.45, overlap)),
                    "reason": "previous_overlap",
                    "matched_markers": [],
                    "tokens": sorted(tokens),
                }

        return {
            "event_id": event.get("event_id"),
            "topic_label": "general",
            "confidence": 0.35,
            "reason": "fallback",
            "matched_markers": [],
            "tokens": sorted(tokens),
        }

    def annotate_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Annotate events in chronological order."""
        annotations: Dict[str, Dict[str, Any]] = {}
        previous: Dict[str, Any] | None = None
        for event in sorted(events, key=lambda item: item.get("valid_time_start", "")):
            annotation = self.annotate_event(event, previous)
            event_id = event.get("event_id")
            if event_id:
                annotations[event_id] = annotation
                previous = annotation
        return annotations

    def majority_topic(self, annotations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return the dominant topic for a window."""
        usable = [
            item for item in annotations
            if item.get("topic_label") and item.get("topic_label") != "general"
        ]
        if not usable:
            return {"topic_label": "general", "confidence": 0.35, "reason": "no_strong_topic"}

        counts = Counter(item["topic_label"] for item in usable)
        topic, count = counts.most_common(1)[0]
        topic_items = [item for item in usable if item["topic_label"] == topic]
        avg_confidence = sum(float(item.get("confidence", 0.0)) for item in topic_items) / len(topic_items)
        return {
            "topic_label": topic,
            "confidence": min(0.95, avg_confidence + min(0.15, 0.03 * count)),
            "reason": "window_majority",
        }

    def _score_markers(self, content_lower: str, tokens: Set[str]) -> List[tuple[str, List[str]]]:
        scored: List[tuple[str, List[str]]] = []
        for topic, markers in self.topic_markers.items():
            matched = []
            for marker in markers:
                marker_lower = marker.lower()
                if marker_lower in content_lower or marker_lower in tokens:
                    matched.append(marker)
            if matched:
                scored.append((topic, matched))
        scored.sort(key=lambda item: len(item[1]), reverse=True)
        return scored
