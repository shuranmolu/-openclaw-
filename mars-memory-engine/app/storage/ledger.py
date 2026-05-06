"""
Raw Event Ledger for MARS Memory Engine.

Handles ingestion of messages into the raw_events table with idempotency support.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import get_db


class RawEventLedger:
    """Manages the raw_events table for storing source messages."""

    def __init__(self, db=None):
        """Initialize ledger.

        Args:
            db: Database instance. If None, uses global instance.
        """
        self.db = db or get_db()

    def _generate_event_id(self) -> str:
        """Generate a unique event ID.

        Returns:
            Event ID string.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        unique = uuid.uuid4().hex[:8]
        return f"evt_{timestamp}_{unique}"

    def ingest_messages(
        self,
        messages: List[Dict[str, Any]],
        project_id: str,
        source_type: str = "sample_json",
        tenant_id: str = "default_tenant",
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest messages into raw_events table with idempotency.

        Args:
            messages: List of message dicts with message_id, actor_id, content, timestamp.
            project_id: Project ID for grouping messages.
            source_type: Type of source (feishu_chat, sample_json, etc.).
            tenant_id: Tenant ID for multi-tenancy.
            chat_id: Optional chat ID.

        Returns:
            Dict with imported_count, skipped_count, event_ids.
        """
        imported_count = 0
        skipped_count = 0
        event_ids = []

        conn = self.db.get_connection()
        cur = conn.cursor()

        for msg in messages:
            message_id = msg.get("message_id")
            if not message_id:
                continue

            # Check for idempotency
            cur.execute(
                "SELECT event_id FROM raw_events WHERE source_type = ? AND source_id = ?",
                (source_type, message_id)
            )
            existing = cur.fetchone()

            if existing:
                skipped_count += 1
                continue

            # Parse timestamp
            timestamp_str = msg.get("timestamp", "")
            try:
                # Handle ISO format with timezone
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str[:-1] + '+00:00'
                valid_time = datetime.fromisoformat(timestamp_str).replace(tzinfo=None).isoformat()
            except (ValueError, AttributeError):
                valid_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            transaction_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

            # Generate event ID
            event_id = self._generate_event_id()

            # Prepare payload JSON
            payload = {
                "message_type": msg.get("message_type", "text"),
                "attachments": msg.get("attachments", []),
                "mentions": msg.get("mentions", []),
            }
            payload_json = json.dumps(payload, ensure_ascii=False)

            # Insert raw event
            cur.execute(
                """
                INSERT INTO raw_events (
                    event_id, event_type, source_type, source_id,
                    tenant_id, project_id, chat_id, actor_id,
                    content, payload_json, transaction_time,
                    valid_time_start, valid_time_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    "message.created",
                    source_type,
                    message_id,
                    tenant_id,
                    project_id,
                    chat_id,
                    msg.get("actor_id", ""),
                    msg.get("content", ""),
                    payload_json,
                    transaction_time,
                    valid_time,
                    None,  # valid_time_end
                )
            )

            imported_count += 1
            event_ids.append(event_id)

        return {
            "imported_count": imported_count,
            "skipped_count": skipped_count,
            "event_ids": event_ids,
        }

    def get_events_by_ids(
        self,
        event_ids: List[str],
    ) -> List[Dict[str, Any]]:
        """Get raw events by their IDs.

        Args:
            event_ids: List of event IDs.

        Returns:
            List of event dicts.
        """
        if not event_ids:
            return []

        conn = self.db.get_connection()
        cur = conn.cursor()

        placeholders = ",".join("?" * len(event_ids))
        query = f"SELECT * FROM raw_events WHERE event_id IN ({placeholders})"

        cur.execute(query, event_ids)
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(raw_events)")
        columns = [col[1] for col in cur.fetchall()]

        return [dict(zip(columns, row)) for row in rows]

    def get_events_by_project(
        self,
        project_id: str,
        limit: Optional[int] = None,
        event_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Get raw events for a project.

        Args:
            project_id: Project ID to filter by.
            limit: Optional maximum number of events to return.
            event_ids: Optional list of event IDs to filter by.

        Returns:
            List of event dicts ordered by valid_time_start.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        query = "SELECT * FROM raw_events WHERE project_id = ?"
        params = [project_id]

        if event_ids:
            placeholders = ",".join("?" * len(event_ids))
            query += f" AND event_id IN ({placeholders})"
            params.extend(event_ids)

        query += " ORDER BY valid_time_start ASC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(raw_events)")
        columns = [col[1] for col in cur.fetchall()]

        return [dict(zip(columns, row)) for row in rows]

    def get_event_count(self, project_id: str) -> int:
        """Get count of events for a project.

        Args:
            project_id: Project ID to count events for.

        Returns:
            Number of events.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*) FROM raw_events WHERE project_id = ?",
            (project_id,)
        )
        return cur.fetchone()[0]
