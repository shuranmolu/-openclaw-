"""
Build extraction context around discussion windows.

This keeps a small bridge from previous windows and recent memory titles so
downstream extractors or OpenClaw can resolve phrases such as "this plan".
"""

from typing import Any, Dict, List


class ContextAssembler:
    def __init__(
        self,
        bridge_count: int = 3,
        topic_history_limit: int = 4,
        memory_title_limit: int = 5,
    ):
        self.bridge_count = bridge_count
        self.topic_history_limit = topic_history_limit
        self.memory_title_limit = memory_title_limit

    def assemble(
        self,
        windows: List[Dict[str, Any]],
        events_map: Dict[str, Dict[str, Any]],
        existing_memories: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        existing_memories = existing_memories or []
        contexts: List[Dict[str, Any]] = []
        topic_history: List[str] = []

        for index, window in enumerate(windows):
            bridge_events = self._bridge_events(windows, index, events_map)
            topic_hint = str(window.get("topic_hint") or "")
            contexts.append({
                "window_id": window.get("window_id"),
                "bridge_event_ids": [event.get("event_id") for event in bridge_events if event.get("event_id")],
                "bridge_snippets": [self._snippet(event.get("content", "")) for event in bridge_events],
                "topic_history": topic_history[-self.topic_history_limit:],
                "recent_memory_titles": self._recent_memory_titles(topic_hint, existing_memories),
            })
            if topic_hint and (not topic_history or topic_history[-1] != topic_hint):
                topic_history.append(topic_hint)

        return contexts

    def _bridge_events(
        self,
        windows: List[Dict[str, Any]],
        current_index: int,
        events_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if current_index <= 0:
            return []
        previous_ids = windows[current_index - 1].get("event_ids", [])[-self.bridge_count:]
        return [events_map[event_id] for event_id in previous_ids if event_id in events_map]

    def _recent_memory_titles(
        self,
        topic_hint: str,
        existing_memories: List[Dict[str, Any]],
    ) -> List[str]:
        if not topic_hint:
            return []
        titles = []
        for memory in existing_memories:
            if memory.get("status") not in {None, "active"}:
                continue
            if str(memory.get("topic") or "").lower() != topic_hint.lower():
                continue
            title = str(memory.get("title") or memory.get("content") or "").strip()
            if title:
                titles.append(title)
        return titles[-self.memory_title_limit:]

    def _snippet(self, text: str, limit: int = 220) -> str:
        clean = " ".join(str(text or "").split())
        return clean if len(clean) <= limit else f"{clean[:limit - 3]}..."
