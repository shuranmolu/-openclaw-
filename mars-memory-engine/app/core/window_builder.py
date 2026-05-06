"""
Window Builder for MARS Memory Engine.

Groups messages into discussion windows for memory extraction.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .topic_tracker import TopicTracker


class WindowBuilder:
    """Builds discussion windows from raw events."""

    # Default window settings
    DEFAULT_TIME_WINDOW_MINUTES = 30
    DEFAULT_MAX_MESSAGES = 50

    def __init__(
        self,
        time_window_minutes: int = DEFAULT_TIME_WINDOW_MINUTES,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        topic_switch_confidence: float = 0.62,
        min_messages_before_topic_split: int = 2,
    ):
        """Initialize window builder.

        Args:
            time_window_minutes: Time window size in minutes.
            max_messages: Maximum messages per window.
        """
        self.time_window = timedelta(minutes=time_window_minutes)
        self.max_messages = max_messages
        self.topic_switch_confidence = topic_switch_confidence
        self.min_messages_before_topic_split = min_messages_before_topic_split
        self.topic_tracker = TopicTracker()

    def build_windows(
        self,
        events: List[Dict[str, Any]],
        project_id: str,
        topic_annotations: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Build discussion windows from raw events.

        Args:
            events: List of raw event dicts from raw_events table.
            project_id: Project ID for the windows.

        Returns:
            List of window dicts with event_ids and time range.
        """
        if not events:
            return []

        # Sort events by time
        sorted_events = sorted(
            events,
            key=lambda e: e.get("valid_time_start", "")
        )

        windows = []
        current_window_events = []
        window_start_time = None
        current_topic = None
        split_reason = "initial"

        for event in sorted_events:
            event_time_str = event.get("valid_time_start", "")
            if not event_time_str:
                continue

            try:
                event_time = datetime.fromisoformat(event_time_str)
            except ValueError:
                continue

            # Start new window
            if window_start_time is None:
                window_start_time = event_time
                current_window_events = [event]
                current_topic = self._event_topic(event, topic_annotations)
                continue

            # Check if we need a new window
            time_diff = event_time - window_start_time
            message_count = len(current_window_events)
            event_topic = self._event_topic(event, topic_annotations)
            topic_changed = self._should_split_on_topic(
                current_topic=current_topic,
                next_topic=event_topic,
                current_message_count=message_count,
            )

            if (time_diff > self.time_window or
                message_count >= self.max_messages or
                topic_changed):
                # Close current window and start new one
                if time_diff > self.time_window:
                    split_reason = "time_window"
                elif message_count >= self.max_messages:
                    split_reason = "max_messages"
                else:
                    split_reason = "topic_shift"
                windows.append(self._create_window(
                    current_window_events,
                    project_id,
                    topic_annotations=topic_annotations,
                    split_reason=split_reason,
                ))
                window_start_time = event_time
                current_window_events = [event]
                current_topic = event_topic
            else:
                # Add to current window
                current_window_events.append(event)
                current_topic = self._merge_topic(current_topic, event_topic)

        # Don't forget the last window
        if current_window_events:
            windows.append(self._create_window(
                current_window_events,
                project_id,
                topic_annotations=topic_annotations,
                split_reason="final",
            ))

        return windows

    def _create_window(
        self,
        events: List[Dict[str, Any]],
        project_id: str,
        topic_annotations: Optional[Dict[str, Dict[str, Any]]] = None,
        split_reason: str = "unknown",
    ) -> Dict[str, Any]:
        """Create a window dict from events.

        Args:
            events: List of events in the window.
            project_id: Project ID.

        Returns:
            Window dict.
        """
        event_ids = [e["event_id"] for e in events]

        # Get time range
        times = [e.get("valid_time_start", "") for e in events if e.get("valid_time_start")]
        times = [t for t in times if t]
        start_time = min(times) if times else ""
        end_time = max(times) if times else ""

        annotations = [
            topic_annotations[e["event_id"]]
            for e in events
            if topic_annotations and e.get("event_id") in topic_annotations
        ]

        if annotations:
            topic_assignment = self.topic_tracker.majority_topic(annotations)
            topic_hint = topic_assignment["topic_label"]
            topic_confidence = topic_assignment["confidence"]
        else:
            # Generate a simple topic hint from content
            topic_hint = self._infer_topic(events)
            topic_confidence = 0.5

        return {
            "window_id": f"win_{uuid.uuid4().hex[:12]}",
            "project_id": project_id,
            "topic_hint": topic_hint,
            "topic_confidence": topic_confidence,
            "split_reason": split_reason,
            "event_ids": event_ids,
            "start_time": start_time,
            "end_time": end_time,
            "message_count": len(events),
        }

    def _event_topic(
        self,
        event: Dict[str, Any],
        topic_annotations: Optional[Dict[str, Dict[str, Any]]],
    ) -> Dict[str, Any]:
        if topic_annotations and event.get("event_id") in topic_annotations:
            return topic_annotations[event["event_id"]]
        return self.topic_tracker.annotate_event(event)

    def _should_split_on_topic(
        self,
        current_topic: Optional[Dict[str, Any]],
        next_topic: Optional[Dict[str, Any]],
        current_message_count: int,
    ) -> bool:
        if not current_topic or not next_topic:
            return False
        if current_message_count < self.min_messages_before_topic_split:
            return False
        left_label = current_topic.get("topic_label")
        right_label = next_topic.get("topic_label")
        if not left_label or not right_label or "general" in {left_label, right_label}:
            return False
        return (
            left_label != right_label
            and float(next_topic.get("confidence", 0.0)) >= self.topic_switch_confidence
        )

    def _merge_topic(
        self,
        current_topic: Optional[Dict[str, Any]],
        next_topic: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        if not current_topic:
            return next_topic
        if not next_topic:
            return current_topic
        if current_topic.get("topic_label") == next_topic.get("topic_label"):
            return current_topic if current_topic.get("confidence", 0) >= next_topic.get("confidence", 0) else next_topic
        return current_topic

    def _infer_topic(self, events: List[Dict[str, Any]]) -> str:
        """Infer a topic hint from events.

        Args:
            events: List of events.

        Returns:
            Topic hint string.
        """
        # Combine all content
        all_content = " ".join([
            e.get("content", "") for e in events
        ]).lower()

        # Simple keyword-based topic inference
        keywords = {
            "技术路线": ["技术", "架构", "框架", "stack", "技术栈"],
            "产品功能": ["功能", "需求", "产品", "用户"],
            "排期计划": ["排期", "时间", "截止", "deadline"],
            "API设计": ["api", "接口", "文档"],
            "测试": ["测试", "qa", "bug"],
            "部署": ["部署", "上线", "发布", "release"],
        }

        for topic, terms in keywords.items():
            if any(term in all_content for term in terms):
                return topic

        return "一般讨论"

    def build_window_from_ids(
        self,
        event_ids: List[str],
        events_map: Dict[str, Dict[str, Any]],
        project_id: str,
    ) -> Dict[str, Any]:
        """Build a window from specific event IDs.

        Args:
            event_ids: List of event IDs.
            events_map: Map of event_id to event dict.
            project_id: Project ID.

        Returns:
            Window dict.
        """
        events = [events_map[eid] for eid in event_ids if eid in events_map]
        return self._create_window(events, project_id)

    def group_by_keyword(
        self,
        events: List[Dict[str, Any]],
        keywords: List[str],
        project_id: str,
    ) -> List[Dict[str, Any]]:
        """Group events by keyword occurrences.

        Args:
            events: List of raw events.
            keywords: List of keywords to group by.
            project_id: Project ID.

        Returns:
            List of windows grouped by keyword.
        """
        if not events or not keywords:
            return []

        keyword_lower = [k.lower() for k in keywords]
        windows = []
        current_group = []

        for event in events:
            content = event.get("content", "").lower()

            # Check if any keyword matches
            has_keyword = any(kw in content for kw in keyword_lower)

            if has_keyword:
                current_group.append(event)
            elif current_group:
                # Close current group
                windows.append(self._create_window(current_group, project_id))
                current_group = []

        # Don't forget the last group
        if current_group:
            windows.append(self._create_window(current_group, project_id))

        return windows
