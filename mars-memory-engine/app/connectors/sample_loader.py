"""
Sample Loader for MARS Memory Engine.

Loads chat messages from local JSON files in Feishu-style format.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class SampleLoader:
    """Loads chat messages from local JSON files."""

    def load_from_file(self, file_path: str) -> Dict[str, Any]:
        """Load chat data from a JSON file.

        Args:
            file_path: Path to JSON file.

        Returns:
            Dict with project_id, chat_id, and messages list.

        Raises:
            FileNotFoundError: If file doesn't exist.
            ValueError: If file format is invalid.
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(path, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)

        # Validate format
        if not isinstance(data, dict):
            raise ValueError("Invalid format: root must be an object")

        # Handle both direct format and wrapped format
        if "messages" in data:
            # Direct format: {project_id, chat_id, messages}
            project_id = data.get("project_id", "default_project")
            chat_id = data.get("chat_id", "default_chat")
            messages = data.get("messages", [])
        elif "plugin_input" in data and "chat" in data["plugin_input"]:
            # QMSum format
            project_id = data.get("case_id", "qmsum_project")
            chat_data = data["plugin_input"]["chat"]
            chat_id = chat_data.get("chat_id", "qmsum_chat")
            messages = []
            for msg in chat_data.get("messages", []):
                messages.append({
                    "message_id": msg.get("message_id", f"msg_{len(messages)}"),
                    "actor_id": msg.get("sender", "Unknown"),
                    "content": msg.get("text", ""),
                    "timestamp": msg.get("timestamp", ""),
                    "message_type": msg.get("message_type", "text"),
                })
        else:
            raise ValueError("Invalid format: missing 'messages' field")

        return {
            "project_id": project_id,
            "chat_id": chat_id,
            "messages": messages,
        }

    def load_from_directory(
        self,
        directory: str,
        pattern: str = "*.json",
    ) -> List[Dict[str, Any]]:
        """Load all chat data from JSON files in a directory.

        Args:
            directory: Path to directory.
            pattern: Glob pattern for files (default: *.json).

        Returns:
            List of chat data dicts.
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {directory}")

        results = []

        for file_path in dir_path.glob(pattern):
            try:
                data = self.load_from_file(str(file_path))
                data["source_file"] = str(file_path)
                results.append(data)
            except (ValueError, json.JSONDecodeError) as e:
                # Skip invalid files
                continue

        return results

    def validate_messages(self, messages: List[Dict[str, Any]]) -> List[str]:
        """Validate messages and return list of errors.

        Args:
            messages: List of message dicts.

        Returns:
            List of error messages (empty if valid).
        """
        errors = []

        for i, msg in enumerate(messages):
            if "message_id" not in msg:
                errors.append(f"Message {i}: missing message_id")
            if "actor_id" not in msg and "sender" not in msg:
                errors.append(f"Message {i}: missing actor_id/sender")
            if "content" not in msg and "text" not in msg:
                errors.append(f"Message {i}: missing content/text")
            if "timestamp" not in msg:
                errors.append(f"Message {i}: missing timestamp")

        return errors

    def normalize_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a message to standard format.

        Args:
            msg: Raw message dict.

        Returns:
            Normalized message dict.
        """
        return {
            "message_id": msg.get("message_id", msg.get("id", "")),
            "actor_id": msg.get("actor_id", msg.get("sender", "Unknown")),
            "content": msg.get("content", msg.get("text", "")),
            "timestamp": msg.get("timestamp", msg.get("time", "")),
            "message_type": msg.get("message_type", msg.get("type", "text")),
            "attachments": msg.get("attachments", []),
            "mentions": msg.get("mentions", []),
        }


def create_carbon_platform_sample(output_path: str) -> None:
    """Create a sample chat file for carbon platform project.

    This file contains a discussion about technology stack decisions:
    - First decides to use Streamlit for phase 1
    - Later changes to Vue + FastAPI

    Args:
        output_path: Path where to save the sample file.
    """
    messages = [
        {
            "message_id": "carbon_msg_001",
            "actor_id": "user_pm",
            "content": "大家好，我们的一期 Demo 需要尽快上线，大家觉得用什么技术栈比较好？",
            "timestamp": "2026-05-01T10:00:00+08:00",
        },
        {
            "message_id": "carbon_msg_002",
            "actor_id": "user_dev1",
            "content": "我建议用 Streamlit，因为我们已经有 Python 数据处理代码了，可以直接复用。",
            "timestamp": "2026-05-01T10:02:00+08:00",
        },
        {
            "message_id": "carbon_msg_003",
            "actor_id": "user_dev2",
            "content": "同意，比赛周期很短，Streamlit 开发速度快，够用就行。",
            "timestamp": "2026-05-01T10:05:00+08:00",
        },
        {
            "message_id": "carbon_msg_004",
            "actor_id": "user_pm",
            "content": "那好，一期先用 Streamlit，正式版再考虑 Vue + FastAPI。",
            "timestamp": "2026-05-01T10:10:00+08:00",
        },
        {
            "message_id": "carbon_msg_005",
            "actor_id": "user_architect",
            "content": "等一下，我重新评估了一下，Streamlit 的工程化程度太弱了。我们应该直接用 Vue + FastAPI。",
            "timestamp": "2026-05-01T14:00:00+08:00",
        },
        {
            "message_id": "carbon_msg_006",
            "actor_id": "user_dev1",
            "content": "但是我们的 Python 代码怎么办？",
            "timestamp": "2026-05-01T14:02:00+08:00",
        },
        {
            "message_id": "carbon_msg_007",
            "actor_id": "user_architect",
            "content": "FastAPI 可以直接调用，前端用 Vue。这样架构更清晰，后续好维护。",
            "timestamp": "2026-05-01T14:05:00+08:00",
        },
        {
            "message_id": "carbon_msg_008",
            "actor_id": "user_pm",
            "content": "有道理。那就改成 Vue + FastAPI 吧。之前的 Streamlit 方案作废。",
            "timestamp": "2026-05-01T14:10:00+08:00",
        },
        {
            "message_id": "carbon_msg_009",
            "actor_id": "user_qa",
            "content": "好的，更新测试计划。API 接口文档明天能给吗？",
            "timestamp": "2026-05-01T14:15:00+08:00",
        },
        {
            "message_id": "carbon_msg_010",
            "actor_id": "user_architect",
            "content": "可以，明天中午前发出来。",
            "timestamp": "2026-05-01T14:16:00+08:00",
        },
    ]

    data = {
        "project_id": "carbon_platform",
        "chat_id": "carbon_tech_discussion",
        "messages": messages,
    }

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
