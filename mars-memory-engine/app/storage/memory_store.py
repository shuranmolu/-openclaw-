"""
Memory Store for MARS Memory Engine.

Manages memory_objects, memory_sources, and memory_edges tables.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import get_db


class MemoryStore:
    """Manages memory objects and their relationships."""

    def __init__(self, db=None):
        """Initialize memory store.

        Args:
            db: Database instance. If None, uses global instance.
        """
        self.db = db or get_db()

    def _generate_memory_id(self) -> str:
        """Generate a unique memory ID.

        Returns:
            Memory ID string.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        unique = uuid.uuid4().hex[:8]
        return f"mem_{timestamp}_{unique}"

    def _generate_source_id(self) -> str:
        """Generate a unique memory source ID.

        Returns:
            Source ID string.
        """
        return f"src_{uuid.uuid4().hex[:12]}"

    def _generate_edge_id(self) -> str:
        """Generate a unique memory edge ID.

        Returns:
            Edge ID string.
        """
        return f"edge_{uuid.uuid4().hex[:12]}"

    def _generate_retrieval_log_id(self) -> str:
        """Generate a unique retrieval log ID."""
        return f"ret_{uuid.uuid4().hex[:12]}"

    def _ensure_retrieval_logs_table(self) -> None:
        """Create retrieval_logs for existing databases that predate the table."""
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_logs (
                log_id TEXT PRIMARY KEY,
                query TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default_tenant',
                project_id TEXT,
                chat_id TEXT,
                requester_id TEXT,
                time_scope TEXT,
                top_k INTEGER,
                status_filter TEXT,
                retrieval_method TEXT,
                retrieved_memory_ids_json TEXT,
                selected_memory_ids_json TEXT,
                score_json TEXT,
                latency_ms INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_project_time ON retrieval_logs(project_id, created_at)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_retrieval_logs_query ON retrieval_logs(query)"
        )

    def log_retrieval(
        self,
        query: str,
        project_id: str,
        retrieved_memory_ids: List[str],
        selected_memory_ids: List[str],
        score_items: List[Dict[str, Any]],
        latency_ms: int,
        time_scope: str = "current",
        top_k: int = 5,
        status_filter: str = "active",
        retrieval_method: str = "keyword_heuristic",
        tenant_id: str = "default_tenant",
        chat_id: Optional[str] = None,
        requester_id: Optional[str] = None,
    ) -> str:
        """Persist an audit log for a memory retrieval operation."""
        self._ensure_retrieval_logs_table()
        log_id = self._generate_retrieval_log_id()
        conn = self.db.get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO retrieval_logs (
                log_id, query, tenant_id, project_id, chat_id, requester_id,
                time_scope, top_k, status_filter, retrieval_method,
                retrieved_memory_ids_json, selected_memory_ids_json, score_json,
                latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                query,
                tenant_id,
                project_id,
                chat_id,
                requester_id,
                time_scope,
                top_k,
                status_filter,
                retrieval_method,
                json.dumps(retrieved_memory_ids, ensure_ascii=False),
                json.dumps(selected_memory_ids, ensure_ascii=False),
                json.dumps(score_items, ensure_ascii=False),
                latency_ms,
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            ),
        )
        return log_id

    def list_retrieval_logs(
        self,
        project_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return recent retrieval audit logs."""
        self._ensure_retrieval_logs_table()
        conn = self.db.get_connection()
        cur = conn.cursor()
        sql = "SELECT * FROM retrieval_logs"
        params: List[Any] = []
        if project_id:
            sql += " WHERE project_id = ?"
            params.append(project_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(200, int(limit))))
        cur.execute(sql, params)
        rows = cur.fetchall()
        columns = [col[0] for col in cur.description]
        logs = []
        for row in rows:
            item = dict(zip(columns, row))
            for field in ["retrieved_memory_ids_json", "selected_memory_ids_json", "score_json"]:
                try:
                    item[field.replace("_json", "")] = json.loads(item.get(field) or "[]")
                except json.JSONDecodeError:
                    item[field.replace("_json", "")] = []
            logs.append(item)
        return logs

    def create_memory(
        self,
        memory_type: str,
        topic: str,
        title: str,
        content: str,
        project_id: str,
        scope: str = "project",
        tenant_id: str = "default_tenant",
        user_id: Optional[str] = None,
        rationale: Optional[List[str]] = None,
        objections: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        status: str = "pending",
        confidence: float = 0.5,
        importance: int = 3,
        source_event_ids: Optional[List[str]] = None,
        valid_time_start: Optional[str] = None,
        valid_time_end: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new memory object with sources.

        Args:
            memory_type: Type of memory (decision, fact, procedure, risk, preference).
            topic: Topic category.
            title: Short title.
            content: Detailed content.
            project_id: Project ID.
            scope: Scope of memory (user, team, project, org).
            tenant_id: Tenant ID.
            user_id: User ID (for scope=user).
            rationale: List of rationale strings.
            objections: List of objection strings.
            tags: List of tag strings.
            status: Status (pending, active, superseded, etc.).
            confidence: Confidence score 0.0-1.0.
            importance: Importance score 1-5.
            source_event_ids: List of raw event IDs as evidence.
            valid_time_start: When memory becomes valid.
            valid_time_end: When memory expires (None = forever).

        Returns:
            Dict with memory_id and created memory.
        """
        memory_id = self._generate_memory_id()

        if valid_time_start is None:
            valid_time_start = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        transaction_time = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

        # Serialize JSON fields
        rationale_json = json.dumps(rationale or [], ensure_ascii=False)
        objections_json = json.dumps(objections or [], ensure_ascii=False)
        tags_json = json.dumps(tags or [], ensure_ascii=False)

        conn = self.db.get_connection()
        cur = conn.cursor()

        # Insert memory object
        cur.execute(
            """
            INSERT INTO memory_objects (
                memory_id, memory_type, scope, tenant_id, project_id, user_id,
                topic, title, content, rationale_json, objections_json, tags_json,
                status, version, confidence, importance,
                valid_time_start, valid_time_end, transaction_time,
                supersedes, superseded_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id, memory_type, scope, tenant_id, project_id, user_id,
                topic, title, content, rationale_json, objections_json, tags_json,
                status, 1, confidence, importance,
                valid_time_start, valid_time_end, transaction_time,
                None, None,  # supersedes, superseded_by
            )
        )

        # Insert memory sources
        if source_event_ids:
            for event_id in source_event_ids:
                source_id = self._generate_source_id()
                cur.execute(
                    """
                    INSERT INTO memory_sources (id, memory_id, event_id, evidence_type, created_at)
                    VALUES (?, ?, ?, 'quote', ?)
                    """,
                    (source_id, memory_id, event_id, transaction_time)
                )

        return {
            "memory_id": memory_id,
            "memory": self.get_memory(memory_id),
        }

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Get a memory by ID.

        Args:
            memory_id: Memory ID.

        Returns:
            Memory dict or None if not found.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute("SELECT * FROM memory_objects WHERE memory_id = ?", (memory_id,))
        row = cur.fetchone()

        if row is None:
            return None

        # Get column names
        cur.execute("PRAGMA table_info(memory_objects)")
        columns = [col[1] for col in cur.fetchall()]

        memory = dict(zip(columns, row))

        # Parse JSON fields
        for field in ["rationale_json", "objections_json", "tags_json"]:
            if memory.get(field):
                try:
                    memory[field.replace("_json", "")] = json.loads(memory[field])
                except json.JSONDecodeError:
                    memory[field.replace("_json", "")] = []

        # Get sources
        memory["sources"] = self.get_memory_sources(memory_id)

        return memory

    def get_memory_sources(self, memory_id: str) -> List[Dict[str, Any]]:
        """Get source events for a memory.

        Args:
            memory_id: Memory ID.

        Returns:
            List of source event dicts.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            SELECT ms.*, re.content, re.valid_time_start
            FROM memory_sources ms
            JOIN raw_events re ON ms.event_id = re.event_id
            WHERE ms.memory_id = ?
            """,
            (memory_id,)
        )
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(memory_sources)")
        source_columns = [col[1] for col in cur.fetchall()]

        sources = []
        for row in rows:
            source = dict(zip(source_columns, row))
            sources.append({
                "source_id": source["id"],
                "event_id": source["event_id"],
                "quote": source.get("quote"),
                "source_url": source.get("source_url"),
                "content": row[len(source_columns)],  # re.content
                "timestamp": row[len(source_columns) + 1],  # re.valid_time_start
            })

        return sources

    def update_memory_status(
        self,
        memory_id: str,
        status: str,
        supersedes: Optional[str] = None,
        superseded_by: Optional[str] = None,
    ) -> bool:
        """Update memory status and version relationships.

        Args:
            memory_id: Memory ID to update.
            status: New status.
            supersedes: Optional memory ID that this memory supersedes.
            superseded_by: Optional memory ID that supersedes this memory.

        Returns:
            True if updated successfully.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        # Update the memory status
        cur.execute(
            """
            UPDATE memory_objects
            SET status = ?, supersedes = ?, superseded_by = ?, updated_at = ?
            WHERE memory_id = ?
            """,
            (status, supersedes, superseded_by, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), memory_id)
        )

        # If this memory supersedes another, update the other memory
        if supersedes:
            cur.execute(
                """
                UPDATE memory_objects
                SET superseded_by = ?, status = 'superseded', updated_at = ?
                WHERE memory_id = ?
                """,
                (memory_id, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), supersedes)
            )

        return True

    def search_memories(
        self,
        project_id: str,
        query: Optional[str] = None,
        memory_types: Optional[List[str]] = None,
        status: str = "active",
        time_scope: str = "current",  # current, all, history
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Search memories with filters.

        Args:
            project_id: Project ID to search in.
            query: Optional keyword query for title/content.
            memory_types: Optional list of memory types to filter.
            status: Status filter (active, all, etc.).
            time_scope: Time scope (current=active, all=all, history=superseded).
            limit: Maximum results to return.

        Returns:
            List of memory dicts with relevance score.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        # Build query
        sql = "SELECT * FROM memory_objects WHERE project_id = ?"
        params = [project_id]

        # Status filter
        if time_scope == "current":
            sql += " AND status = 'active'"
        elif time_scope == "history":
            sql += " AND status IN ('superseded', 'expired')"
        # all: no filter

        # Type filter
        if memory_types:
            placeholders = ",".join("?" * len(memory_types))
            sql += f" AND memory_type IN ({placeholders})"
            params.extend(memory_types)

        # Keyword search
        if query:
            sql += " AND (title LIKE ? OR content LIKE ? OR topic LIKE ?)"
            keyword = f"%{query}%"
            params.extend([keyword, keyword, keyword])

        sql += " ORDER BY importance DESC, created_at DESC LIMIT ?"
        params.append(limit)

        cur.execute(sql, params)
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(memory_objects)")
        columns = [col[1] for col in cur.fetchall()]

        results = []
        for row in rows:
            memory = dict(zip(columns, row))

            # Parse JSON fields
            for field in ["rationale_json", "objections_json", "tags_json"]:
                if memory.get(field):
                    try:
                        memory[field.replace("_json", "")] = json.loads(memory[field])
                    except json.JSONDecodeError:
                        memory[field.replace("_json", "")] = []

            # Calculate simple relevance score
            score = 1.0
            if query:
                title_lower = memory.get("title", "").lower()
                content_lower = memory.get("content", "").lower()
                query_lower = query.lower()

                if query_lower in title_lower:
                    score = 1.0
                elif query_lower in content_lower:
                    score = 0.8
                else:
                    score = 0.5

                # Boost by importance
                score *= (memory.get("importance", 3) / 5)

            memory["score"] = score
            memory["sources"] = self.get_memory_sources(memory["memory_id"])

            results.append(memory)

        return results

    def create_edge(
        self,
        source_memory_id: str,
        target_memory_id: str,
        relation_type: str,
        reason: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> str:
        """Create a relationship edge between two memories.

        Args:
            source_memory_id: Source memory ID.
            target_memory_id: Target memory ID.
            relation_type: Type of relation (duplicate, support, conflict, supersede, etc.).
            reason: Reason for the relationship.
            confidence: Confidence score.

        Returns:
            Edge ID.
        """
        edge_id = self._generate_edge_id()

        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO memory_edges (edge_id, source_memory_id, target_memory_id, relation_type, reason, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (edge_id, source_memory_id, target_memory_id, relation_type, reason, confidence)
        )

        return edge_id

    def get_active_memories(
        self,
        project_id: str,
        memory_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get all active memories for a project.

        Args:
            project_id: Project ID.
            memory_type: Optional memory type filter.

        Returns:
            List of active memory dicts.
        """
        return self.search_memories(
            project_id=project_id,
            memory_types=[memory_type] if memory_type else None,
            status="active",
            time_scope="current",
            limit=1000,
        )

    def get_memory_by_topic(
        self,
        project_id: str,
        topic: str,
        status: str = "active",
    ) -> List[Dict[str, Any]]:
        """Get memories by topic.

        Args:
            project_id: Project ID.
            topic: Topic to search for.
            status: Status filter.

        Returns:
            List of memory dicts.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        sql = "SELECT * FROM memory_objects WHERE project_id = ? AND topic = ?"
        params = [project_id, topic]

        if status != "all":
            sql += " AND status = ?"
            params.append(status)

        sql += " ORDER BY created_at DESC"

        cur.execute(sql, params)
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(memory_objects)")
        columns = [col[1] for col in cur.fetchall()]

        results = []
        for row in rows:
            memory = dict(zip(columns, row))
            # Parse JSON fields
            for field in ["rationale_json", "objections_json", "tags_json"]:
                if memory.get(field):
                    try:
                        memory[field.replace("_json", "")] = json.loads(memory[field])
                    except json.JSONDecodeError:
                        memory[field.replace("_json", "")] = []
            results.append(memory)

        return results

    def save_candidate(
        self,
        candidate_type: str,
        topic: str,
        summary: str,
        project_id: str,
        evidence_event_ids: List[str],
        confidence: float = 0.5,
        need_human_confirm: bool = False,
    ) -> str:
        """Save a memory candidate.

        Args:
            candidate_type: Type of candidate (decision, fact, procedure, risk).
            topic: Topic category.
            summary: Summary text.
            project_id: Project ID.
            evidence_event_ids: List of evidence event IDs.
            confidence: Confidence score.
            need_human_confirm: Whether human confirmation is needed.

        Returns:
            Candidate ID.
        """
        candidate_id = f"cand_{uuid.uuid4().hex[:12]}"

        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO memory_candidates (
                candidate_id, candidate_type, topic, summary,
                project_id, evidence_event_ids, confidence, need_human_confirm
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                candidate_type,
                topic,
                summary,
                project_id,
                json.dumps(evidence_event_ids, ensure_ascii=False),
                confidence,
                1 if need_human_confirm else 0,
            )
        )

        return candidate_id

    def get_candidates(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all candidates for a project.

        Args:
            project_id: Project ID.

        Returns:
            List of candidate dicts.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        cur.execute(
            "SELECT * FROM memory_candidates WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,)
        )
        rows = cur.fetchall()

        # Get column names
        cur.execute("PRAGMA table_info(memory_candidates)")
        columns = [col[1] for col in cur.fetchall()]

        results = []
        for row in rows:
            candidate = dict(zip(columns, row))
            # Parse evidence_event_ids
            if candidate.get("evidence_event_ids"):
                try:
                    candidate["evidence_event_ids"] = json.loads(candidate["evidence_event_ids"])
                except json.JSONDecodeError:
                    candidate["evidence_event_ids"] = []
            results.append(candidate)

        return results

    def commit_candidate(
        self,
        candidate_id: str,
        title: Optional[str] = None,
        rationale: Optional[List[str]] = None,
        status: str = "active",
    ) -> Optional[Dict[str, Any]]:
        """Commit a candidate to a full memory object.

        Args:
            candidate_id: Candidate ID to commit.
            title: Optional title override.
            rationale: Optional rationale list.
            status: Status for committed memory.

        Returns:
            Created memory dict or None if candidate not found.
        """
        conn = self.db.get_connection()
        cur = conn.cursor()

        # Get candidate
        cur.execute(
            "SELECT * FROM memory_candidates WHERE candidate_id = ?",
            (candidate_id,)
        )
        row = cur.fetchone()

        if row is None:
            return None

        # Get column names
        cur.execute("PRAGMA table_info(memory_candidates)")
        columns = [col[1] for col in cur.fetchall()]
        candidate = dict(zip(columns, row))

        # Parse evidence_event_ids
        try:
            evidence_event_ids = json.loads(candidate["evidence_event_ids"])
        except json.JSONDecodeError:
            evidence_event_ids = []

        # Get the earliest event timestamp as valid_time_start
        valid_time_start = None
        if evidence_event_ids:
            placeholders = ",".join("?" * len(evidence_event_ids))
            cur.execute(
                f"SELECT MIN(valid_time_start) as min_time FROM raw_events WHERE event_id IN ({placeholders})",
                evidence_event_ids
            )
            row = cur.fetchone()
            if row and row[0]:
                valid_time_start = row[0]

        # Create memory from candidate
        result = self.create_memory(
            memory_type=candidate["candidate_type"],
            topic=candidate["topic"],
            title=title or f"{candidate['topic'].capitalize()} Decision",
            content=candidate["summary"],
            project_id=candidate["project_id"],
            confidence=candidate["confidence"],
            source_event_ids=evidence_event_ids,
            rationale=rationale,
            status=status,
            valid_time_start=valid_time_start,
        )

        # Delete candidate
        cur.execute("DELETE FROM memory_candidates WHERE candidate_id = ?", (candidate_id,))

        return result
