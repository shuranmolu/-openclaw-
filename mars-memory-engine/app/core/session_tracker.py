"""
Session-level topic state machine for event streams.

Unlike TopicTracker, this remembers previous topics and can mark an event as a
continuation, resume, drift, or new topic.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Set

from .topic_tracker import TopicTracker, tokenize_text, token_overlap_ratio


TopicStateLabel = Literal["new_topic", "continuation", "resume", "drift"]


@dataclass
class SessionTopic:
    label: str
    tokens: Set[str]
    last_seen_time: str
    event_count: int = 1
    status: str = "active"


class SessionTracker:
    def __init__(
        self,
        continuation_threshold: float = 0.18,
        resume_threshold: float = 0.28,
        drift_threshold: float = 0.08,
        time_gap_new_topic_minutes: float = 60.0,
    ):
        self.continuation_threshold = continuation_threshold
        self.resume_threshold = resume_threshold
        self.drift_threshold = drift_threshold
        self.time_gap_new_topic_minutes = time_gap_new_topic_minutes
        self.topic_tracker = TopicTracker()
        self.topics: List[SessionTopic] = []
        self.current_topic: Optional[SessionTopic] = None
        self._counter = 0

    def annotate_batch(self, events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        annotations: Dict[str, Dict[str, Any]] = {}
        for event in sorted(events, key=lambda item: item.get("valid_time_start", "")):
            annotation = self.annotate(event)
            event_id = event.get("event_id")
            if event_id:
                annotations[event_id] = annotation
        return annotations

    def annotate(self, event: Dict[str, Any]) -> Dict[str, Any]:
        tokens = tokenize_text(event.get("content", "") or "")
        event_time = event.get("valid_time_start") or event.get("transaction_time") or ""
        marker = self.topic_tracker.annotate_event(event)
        marker_label = marker.get("topic_label")

        if self.current_topic is None:
            return self._start(event, tokens, event_time, marker_label)

        if self._minutes_gap(self.current_topic.last_seen_time, event_time) > self.time_gap_new_topic_minutes:
            resume = self._best_resume(tokens, exclude=self.current_topic)
            if resume and resume[1] >= self.resume_threshold:
                return self._resume(event, resume[0], tokens, event_time, resume[1])
            return self._start(event, tokens, event_time, marker_label)

        current_overlap = token_overlap_ratio(tokens, self.current_topic.tokens)
        resume = self._best_resume(tokens, exclude=self.current_topic)
        if resume and resume[1] >= self.resume_threshold and resume[1] > current_overlap + 0.1:
            return self._resume(event, resume[0], tokens, event_time, resume[1])

        if marker_label and marker_label != "general" and marker_label != self.current_topic.label:
            for topic in self.topics:
                if topic.label == marker_label and topic is not self.current_topic:
                    return self._resume(event, topic, tokens, event_time, 0.5)
            return self._start(event, tokens, event_time, marker_label)

        if current_overlap >= self.continuation_threshold:
            return self._continue(event, tokens, event_time, current_overlap)

        if current_overlap >= self.drift_threshold:
            return self._drift(event, tokens, event_time, current_overlap)

        return self._start(event, tokens, event_time, marker_label)

    def _start(
        self,
        event: Dict[str, Any],
        tokens: Set[str],
        event_time: str,
        marker_label: Optional[str],
    ) -> Dict[str, Any]:
        if self.current_topic:
            self.current_topic.status = "paused"
        label = marker_label if marker_label and marker_label != "general" else self._new_label()
        topic = SessionTopic(label=label, tokens=set(tokens), last_seen_time=event_time)
        self.topics.append(topic)
        self.current_topic = topic
        return self._annotation(event, "new_topic", label, 0.6)

    def _continue(
        self,
        event: Dict[str, Any],
        tokens: Set[str],
        event_time: str,
        overlap: float,
    ) -> Dict[str, Any]:
        assert self.current_topic is not None
        self.current_topic.tokens.update(tokens)
        self.current_topic.last_seen_time = event_time
        self.current_topic.event_count += 1
        return self._annotation(event, "continuation", self.current_topic.label, min(1.0, 0.5 + overlap))

    def _resume(
        self,
        event: Dict[str, Any],
        topic: SessionTopic,
        tokens: Set[str],
        event_time: str,
        overlap: float,
    ) -> Dict[str, Any]:
        if self.current_topic:
            self.current_topic.status = "paused"
        topic.status = "active"
        topic.tokens.update(tokens)
        topic.last_seen_time = event_time
        topic.event_count += 1
        self.current_topic = topic
        return self._annotation(event, "resume", topic.label, min(1.0, 0.55 + overlap))

    def _drift(
        self,
        event: Dict[str, Any],
        tokens: Set[str],
        event_time: str,
        overlap: float,
    ) -> Dict[str, Any]:
        assert self.current_topic is not None
        self.current_topic.tokens.update(tokens)
        self.current_topic.last_seen_time = event_time
        self.current_topic.event_count += 1
        return self._annotation(event, "drift", self.current_topic.label, min(0.65, 0.4 + overlap))

    def _best_resume(
        self,
        tokens: Set[str],
        exclude: Optional[SessionTopic],
    ) -> Optional[tuple[SessionTopic, float]]:
        best: Optional[tuple[SessionTopic, float]] = None
        for topic in self.topics:
            if topic is exclude:
                continue
            overlap = token_overlap_ratio(tokens, topic.tokens)
            if best is None or overlap > best[1]:
                best = (topic, overlap)
        return best

    def _annotation(
        self,
        event: Dict[str, Any],
        state: TopicStateLabel,
        label: str,
        confidence: float,
    ) -> Dict[str, Any]:
        return {
            "event_id": event.get("event_id"),
            "topic_state": state,
            "topic_label": label,
            "confidence": round(float(confidence), 3),
        }

    def _new_label(self) -> str:
        self._counter += 1
        return f"session_topic_{self._counter}"

    def _minutes_gap(self, left: str, right: str) -> float:
        try:
            left_dt = datetime.fromisoformat(str(left))
            right_dt = datetime.fromisoformat(str(right))
        except ValueError:
            return 0.0
        return abs((right_dt - left_dt).total_seconds()) / 60.0
