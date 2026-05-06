"""
MARS Memory Engine Service.

Provides high-level API functions for memory operations.
"""

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Optional

from .connectors.sample_loader import SampleLoader
from .core.answerer import MemoryAnswerer
from .core.consolidator import MemoryConsolidator
from .core.context_assembler import ContextAssembler
from .core.extractor import RuleBasedExtractor
from .core.post_processor import CandidatePostProcessor
from .core.query_planner import QueryPlanner
from .core.reconciler import MemoryReconciler, auto_reconcile_updates
from .core.retriever import MemoryRetriever
from .core.session_tracker import SessionTracker
from .core.topic_tracker import TopicTracker
from .core.window_builder import WindowBuilder
from .llm import get_llm_provider
from .models import DecisionCard, EvidenceChain, EvidenceItem
from .storage.db import get_db
from .storage.ledger import RawEventLedger
from .storage.memory_store import MemoryStore


class MarsService:
    """Main service for MARS Memory Engine."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize service.

        Args:
            db_path: Optional path to database file.
        """
        self.db = get_db(db_path)
        self.ledger = RawEventLedger(self.db)
        self.memory_store = MemoryStore(self.db)
        self.window_builder = WindowBuilder()
        self.topic_tracker = TopicTracker()
        self.session_tracker = SessionTracker()
        self.context_assembler = ContextAssembler()
        self.extractor = RuleBasedExtractor()
        self.post_processor = CandidatePostProcessor()
        self.consolidator = MemoryConsolidator()
        self.query_planner = QueryPlanner()
        self.answerer = MemoryAnswerer()
        self.llm_provider = get_llm_provider()
        self.retriever = MemoryRetriever(self.memory_store)
        self.reconciler = MemoryReconciler(self.memory_store)
        self.sample_loader = SampleLoader()

    def mars_ingest_messages(
        self,
        project_id: str,
        messages: List[Dict[str, Any]],
        chat_id: Optional[str] = None,
        tenant_id: str = "default_tenant",
    ) -> Dict[str, Any]:
        """Ingest messages into raw_events table.

        Args:
            project_id: Project ID.
            messages: List of message dicts.
            chat_id: Optional chat ID.
            tenant_id: Tenant ID.

        Returns:
            Dict with imported_count, skipped_count, event_ids.
        """
        return self.ledger.ingest_messages(
            messages=messages,
            project_id=project_id,
            source_type="feishu_chat",
            tenant_id=tenant_id,
            chat_id=chat_id,
        )

    def mars_ingest_from_file(
        self,
        file_path: str,
    ) -> Dict[str, Any]:
        """Ingest messages from a JSON file.

        Args:
            file_path: Path to JSON file.

        Returns:
            Dict with imported_count, skipped_count, event_ids, project_id.
        """
        # Load file
        data = self.sample_loader.load_from_file(file_path)

        # Normalize messages
        normalized_messages = [
            self.sample_loader.normalize_message(msg)
            for msg in data["messages"]
        ]

        # Ingest
        result = self.mars_ingest_messages(
            project_id=data["project_id"],
            messages=normalized_messages,
            chat_id=data["chat_id"],
        )

        result["project_id"] = data["project_id"]
        return result

    def mars_ingest_text(
        self,
        project_id: str,
        text: str,
        title: Optional[str] = None,
        source_id: Optional[str] = None,
        actor_id: str = "feishu_doc",
        chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest text content by splitting into messages.

        Args:
            project_id: Project ID.
            text: Text content to ingest.
            title: Optional title for the document (used in idempotency key).
            source_id: Optional source ID for idempotency.
            actor_id: Actor ID for the messages (default: "feishu_doc").
            chat_id: Optional chat ID.

        Returns:
            Dict with imported_count, skipped_count, event_ids.
        """
        # Generate a stable document ID for idempotency
        if source_id:
            doc_id = source_id
        elif title:
            # Use title + content hash for idempotency
            content_hash = hashlib.md5(text.encode()).hexdigest()[:8]
            doc_id = f"{title}_{content_hash}"
        else:
            # Use only content hash for idempotency
            content_hash = hashlib.md5(text.encode()).hexdigest()[:12]
            doc_id = f"doc_{content_hash}"

        # Split text into chunks (800-1200 chars, preferring paragraph boundaries)
        chunks = self._split_text_into_chunks(text, max_chunk_size=1000)

        # Convert chunks to messages with stable message_ids for idempotency
        messages = []
        for i, chunk in enumerate(chunks):
            # Use doc_id + chunk index for stable message_id
            message_id = f"{doc_id}_chunk_{i}"
            messages.append({
                "message_id": message_id,
                "actor_id": actor_id,
                "content": chunk,
                "timestamp": "",
                "message_type": "text",
            })

        # Ingest messages
        return self.mars_ingest_messages(
            project_id=project_id,
            messages=messages,
            chat_id=chat_id,
        )

    def _split_text_into_chunks(
        self,
        text: str,
        max_chunk_size: int = 1000,
        min_chunk_size: int = 400,
    ) -> List[str]:
        """Split text into chunks, preferring paragraph boundaries.

        Args:
            text: Input text.
            max_chunk_size: Maximum chunk size in characters.
            min_chunk_size: Minimum chunk size to avoid breaking.

        Returns:
            List of text chunks.
        """
        # Normalize line endings and trim
        text = text.replace("\r\n", "\n").strip()
        if not text:
            return []

        # Split by paragraphs (double newlines)
        paragraphs = re.split(r"\n\s*\n", text)

        chunks = []
        current_chunk = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # If adding this paragraph would exceed max size and current chunk is large enough, flush it
            if current_chunk and len(current_chunk) + len(para) + 2 > max_chunk_size:
                if len(current_chunk) >= min_chunk_size:
                    chunks.append(current_chunk.strip())
                    current_chunk = para
                else:
                    # Paragraph is too large, need to split it
                    if len(para) > max_chunk_size:
                        # Flush current chunk first
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = ""
                        # Split large paragraph by sentences
                        sentences = re.split(r"(?<=[。！？.!?])\s+", para)
                        for sentence in sentences:
                            sentence = sentence.strip()
                            if not sentence:
                                continue
                            if len(current_chunk) + len(sentence) + 1 > max_chunk_size:
                                if current_chunk:
                                    chunks.append(current_chunk.strip())
                                current_chunk = sentence
                            else:
                                current_chunk += (" " if current_chunk else "") + sentence
                    else:
                        current_chunk += ("\n\n" if current_chunk else "") + para
            else:
                current_chunk += ("\n\n" if current_chunk else "") + para

        # Add remaining chunk
        if current_chunk:
            chunks.append(current_chunk.strip())

        return chunks

    def mars_digest(
        self,
        project_id: str,
        event_ids: Optional[List[str]] = None,
        message_count: Optional[int] = None,
        auto_commit: bool = False,
    ) -> Dict[str, Any]:
        """Extract candidate memories from events.

        Args:
            project_id: Project ID.
            event_ids: Optional list of specific event IDs.
            message_count: Optional recent message count.
            auto_commit: Whether to auto-commit high-confidence candidates.

        Returns:
            Dict with candidates, committed_count, memory_ids.
        """
        # Get events
        if event_ids:
            events = self.ledger.get_events_by_ids(event_ids)
        else:
            events = self.ledger.get_events_by_project(
                project_id=project_id,
                limit=message_count,
            )

        if not events:
            return {
                "candidates": [],
                "dropped_candidates": [],
                "committed_count": 0,
                "memory_ids": [],
                "windows": [],
                "topic_annotations": [],
                "session_annotations": [],
                "extraction_contexts": [],
            }

        # Build windows
        events_map = {e["event_id"]: e for e in events}
        topic_annotations = self.topic_tracker.annotate_batch(events)
        session_annotations = SessionTracker().annotate_batch(events)
        windows = self.window_builder.build_windows(
            events,
            project_id,
            topic_annotations=topic_annotations,
        )
        existing_memories = self.memory_store.search_memories(
            project_id=project_id,
            status="active",
            time_scope="current",
            limit=200,
        )
        extraction_contexts = self.context_assembler.assemble(
            windows,
            events_map,
            existing_memories=existing_memories,
        )

        # Extract candidates
        candidates = self.extractor.extract_candidates(windows, events_map)
        windows_by_id = {w["window_id"]: w for w in windows}
        candidates, dropped_candidates = self.post_processor.process_candidates(
            candidates,
            windows_by_id=windows_by_id,
        )
        consolidation = self.consolidator.propose(candidates)

        # Optionally commit high-confidence candidates
        committed_count = 0
        memory_ids = []

        if auto_commit:
            for candidate in candidates:
                if candidate["confidence"] >= 0.7 and not candidate["need_human_confirm"]:
                    # Save candidate first
                    cand_id = self.memory_store.save_candidate(
                        candidate_type=candidate["candidate_type"],
                        topic=candidate["topic"],
                        summary=candidate["summary"],
                        project_id=candidate["project_id"],
                        evidence_event_ids=candidate["evidence_event_ids"],
                        confidence=candidate["confidence"],
                        need_human_confirm=candidate["need_human_confirm"],
                    )

                    # Commit to memory
                    result = self.memory_store.commit_candidate(cand_id, status="active")
                    if result:
                        committed_count += 1
                        memory_ids.append(result["memory_id"])
        else:
            # Just save candidates without committing
            for candidate in candidates:
                self.memory_store.save_candidate(
                    candidate_type=candidate["candidate_type"],
                    topic=candidate["topic"],
                    summary=candidate["summary"],
                    project_id=candidate["project_id"],
                    evidence_event_ids=candidate["evidence_event_ids"],
                    confidence=candidate["confidence"],
                    need_human_confirm=candidate["need_human_confirm"],
                )

        return {
            "candidates": candidates,
            "dropped_candidates": dropped_candidates,
            "committed_count": committed_count,
            "memory_ids": memory_ids,
            "windows": [
                {
                    "window_id": window["window_id"],
                    "topic_hint": window.get("topic_hint"),
                    "topic_confidence": window.get("topic_confidence"),
                    "split_reason": window.get("split_reason"),
                    "message_count": window.get("message_count"),
                    "event_ids": window.get("event_ids", []),
                }
                for window in windows
            ],
            "topic_annotations": [
                {
                    "event_id": item.get("event_id"),
                    "topic_label": item.get("topic_label"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                    "matched_markers": item.get("matched_markers", []),
                }
                for item in topic_annotations.values()
            ],
            "session_annotations": [
                {
                    "event_id": item.get("event_id"),
                    "topic_state": item.get("topic_state"),
                    "topic_label": item.get("topic_label"),
                    "confidence": item.get("confidence"),
                }
                for item in session_annotations.values()
            ],
            "extraction_contexts": extraction_contexts,
            "consolidation": consolidation,
        }

    def mars_consolidate_project(
        self,
        project_id: str,
        include_candidates: bool = True,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """Return duplicate/update/conflict proposals for a project.

        This is advisory only. OpenClaw can use the proposals as evidence before
        deciding whether to merge, supersede, or ask for review.
        """
        memories = self.memory_store.search_memories(
            project_id=project_id,
            status="all",
            time_scope="all",
            limit=1000,
        )
        items: List[Dict[str, Any]] = list(memories)
        if include_candidates:
            items.extend(self.memory_store.get_candidates(project_id))
        return self.consolidator.propose(items, max_proposals=limit)

    def mars_search(
        self,
        project_id: str,
        query: str,
        time_scope: str = "current",
        memory_types: Optional[List[str]] = None,
        top_k: int = 5,
        include_evidence: bool = True,
    ) -> Dict[str, Any]:
        """Search memories.

        Args:
            project_id: Project ID.
            query: Search query.
            time_scope: Time scope (current, all, history).
            memory_types: Optional memory type filter.
            top_k: Maximum results.
            include_evidence: Include source evidence.

        Returns:
            Dict with answer, memories, total_retrieved, latency_ms.
        """
        plan = self.query_planner.plan(query, top_k=top_k)
        effective_memory_types = memory_types or plan.preferred_types
        result = self.retriever.search(
            project_id=project_id,
            query=query,
            time_scope=time_scope,
            memory_types=effective_memory_types,
            top_k=top_k,
            include_evidence=include_evidence,
        )
        answer_bundle = self.answerer.compose(plan, result.get("memories", []))
        result["answer"] = answer_bundle["answer"]
        result["answer_bundle"] = answer_bundle
        result["query_plan"] = plan.to_dict()
        result["memory_types_used"] = effective_memory_types
        return result

    def mars_find_similar_decisions(
        self,
        project_id: str,
        query: str,
        text: Optional[str] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        """Find similar decisions and classify their relationship.

        Args:
            project_id: Project ID.
            query: Search query for finding similar decisions.
            text: Optional full text for more detailed comparison.
            top_k: Maximum number of similar decisions to return.

        Returns:
            Dict with similar_decisions list.
        """
        return self.retriever.find_similar_decisions(
            project_id=project_id,
            query=query,
            text=text,
            top_k=top_k,
        )

    def mars_process_command(
        self,
        project_id: str,
        command_text: str,
        context_text: Optional[str] = None,
        title: Optional[str] = None,
        source_id: Optional[str] = None,
        query: Optional[str] = None,
        agent_summary: Optional[str] = None,
        agent_lifecycle_decision: Optional[Dict[str, Any]] = None,
        agent_structured_card: Optional[Dict[str, Any]] = None,
        auto_commit: bool = False,
    ) -> Dict[str, Any]:
        """Process a natural language command for decision memory.

        This is the main entry point for Feishu @ commands.

        Args:
            project_id: Project ID.
            command_text: Natural language command text.
            context_text: Optional context for the command.
            title: Optional title for document source.
            source_id: Optional source ID for idempotency.
            query: Optional query for searching similar decisions.
            agent_summary: Optional OpenClaw-generated full-coverage summary.
            agent_lifecycle_decision: Optional OpenClaw judgment for new/update/conflict/duplicate.
            agent_structured_card: Optional OpenClaw structured decision extraction.
            auto_commit: Whether to auto-commit high-confidence candidates.

        Returns:
            Dict with decision_card preview and similar_decisions.
        """
        digest_result: Dict[str, Any] = {"candidates": [], "committed_count": 0}

        # If context_text provided, ingest it first and extract preview candidates.
        if context_text:
            self.mars_ingest_text(
                project_id=project_id,
                text=context_text,
                title=title,
                source_id=source_id,
            )
            digest_result = self.mars_digest(
                project_id=project_id,
                auto_commit=auto_commit,
            )

        # Find similar decisions
        search_query = query or command_text
        comparison_text = "\n".join(
            part for part in [agent_summary, context_text, command_text] if part
        )
        similar_result = self.mars_find_similar_decisions(
            project_id=project_id,
            query=search_query,
            text=comparison_text,
            top_k=5,
        )

        # Get current memories for decision card
        search_result = self.mars_search(
            project_id=project_id,
            query=search_query,
            top_k=8,
            include_evidence=True,
        )

        # Build decision card with lifecycle info
        decision_card = self._build_decision_card_with_lifecycle(
            command_text=command_text,
            search_result=search_result,
            similar_result=similar_result,
            project_id=project_id,
            candidates=digest_result.get("candidates", []),
            agent_summary=agent_summary,
            agent_lifecycle_decision=agent_lifecycle_decision,
            agent_structured_card=agent_structured_card,
        )

        return {
            "decision_card": decision_card,
            "similar_decisions": similar_result.get("similar_decisions", []),
            "agent_summary_used": bool(agent_summary),
            "agent_lifecycle_decision_used": bool(agent_lifecycle_decision),
            "agent_structured_card_used": bool(agent_structured_card),
            "auto_commit": auto_commit,
            "candidate_count": len(digest_result.get("candidates", [])),
            "committed_count": digest_result.get("committed_count", 0),
        }

    def mars_evaluate_decision_card(
        self,
        generated_card: Dict[str, Any],
        reference_card: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Evaluate a generated card through the configured LLM boundary."""
        result = self.llm_provider.evaluate_card(generated_card, reference_card)
        return result.model_dump(mode="json")

    def _build_decision_card_with_lifecycle(
        self,
        command_text: str,
        search_result: Dict[str, Any],
        similar_result: Dict[str, Any],
        project_id: str,
        candidates: Optional[List[Dict[str, Any]]] = None,
        agent_summary: Optional[str] = None,
        agent_lifecycle_decision: Optional[Dict[str, Any]] = None,
        agent_structured_card: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build decision card with lifecycle information.

        Args:
            command_text: Original command text.
            search_result: Search result from mars_search.
            similar_result: Result from mars_find_similar_decisions.
            project_id: Project ID.

        Returns:
            Decision card dict with lifecycle fields.
        """
        memories = search_result.get("memories", [])
        similar_decisions = similar_result.get("similar_decisions", [])
        candidates = candidates or []

        # Extract decisions and evidence
        decisions = []
        action_items = []
        open_questions = []
        evidence_ids = []
        structured = self._normalize_agent_structured_card(agent_structured_card)

        if agent_summary:
            decisions.extend(self._split_agent_summary(agent_summary))

        for memory in memories:
            content = memory.get("content", "")
            if content:
                decisions.append(content)
            for evidence in memory.get("evidence", []):
                if evidence.get("event_id"):
                    evidence_ids.append(evidence["event_id"])

        for candidate in candidates:
            summary = candidate.get("summary", "")
            if summary and summary not in decisions:
                decisions.append(summary)
            for event_id in candidate.get("evidence_event_ids", []):
                if event_id not in evidence_ids:
                    evidence_ids.append(event_id)

        # Analyze similar decisions for lifecycle status
        has_duplicate = any(d.get("relation") == "duplicate" for d in similar_decisions)
        has_conflict = any(d.get("relation") == "conflict" for d in similar_decisions)
        has_update = any(d.get("relation") == "update" for d in similar_decisions)

        # Determine lifecycle status
        if has_duplicate:
            lifecycle_status = "duplicate"
            recommended_action = "review_existing"
        elif has_conflict:
            lifecycle_status = "conflict"
            recommended_action = "resolve_conflict"
        elif has_update:
            lifecycle_status = "update"
            recommended_action = "update_existing"
        else:
            lifecycle_status = "new"
            recommended_action = "create_new"

        heuristic_status = lifecycle_status
        agent_decision = self._normalize_agent_lifecycle_decision(agent_lifecycle_decision)
        if agent_decision:
            lifecycle_status = agent_decision["status"]
            recommended_action = agent_decision.get("recommended_action") or {
                "duplicate": "review_existing",
                "conflict": "resolve_conflict",
                "update": "update_existing",
                "new": "create_new",
            }.get(lifecycle_status, recommended_action)

        # Build conflicts list
        conflicts = [
            d for d in similar_decisions
            if d.get("relation") in ("conflict", "duplicate")
        ]
        evidence_chain = self._build_evidence_chain(
            project_id=project_id,
            search_result=search_result,
            candidates=candidates,
            evidence_ids=evidence_ids,
            structured=structured,
            decisions=decisions,
        )

        raw_card = {
            "title": f"决策卡：{command_text[:50]}" if len(command_text) > 50 else f"决策卡：{command_text}",
            "summary": decisions[:6] if decisions else [f"基于命令「{command_text}」生成的决策预览"],
            "decisions": decisions[:8],
            "decision_items": structured["decision_items"],
            "reasons": structured["reasons"],
            "objections": structured["objections"],
            "conclusions": structured["conclusions"],
            "project_phase": structured["project_phase"],
            "time_points": structured["time_points"],
            "topic_links": structured["topic_links"],
            "source_scope": structured["source_scope"],
            "action_items": action_items,
            "open_questions": open_questions if open_questions else ["需要确认是否创建此决策"],
            "evidence_message_ids": evidence_ids[:12],
            "evidence_chain": evidence_chain.to_dict(),
            "participants": [],
            "confidence": 0.5 if not decisions else 0.7,
            "project_id": project_id,
            "query": command_text,
            # Lifecycle fields
            "lifecycle": {
                "status": lifecycle_status,
                "heuristic_status": heuristic_status,
                "agent_decision": agent_decision,
                "similar_decisions": similar_decisions,
                "conflicts": conflicts,
                "recommended_action": recommended_action,
                "requires_confirmation": True,
            },
        }
        return DecisionCard.model_validate(raw_card).to_dict()

    def _build_evidence_chain(
        self,
        project_id: str,
        search_result: Dict[str, Any],
        candidates: List[Dict[str, Any]],
        evidence_ids: List[str],
        structured: Dict[str, Any],
        decisions: List[str],
    ) -> EvidenceChain:
        """Build a traceable evidence chain for a generated decision card."""
        items_by_id: Dict[str, EvidenceItem] = {}

        for memory in search_result.get("memories", []):
            memory_id = str(memory.get("memory_id") or "")
            for evidence in memory.get("evidence", []):
                event_id = str(evidence.get("event_id") or "").strip()
                if not event_id:
                    continue
                items_by_id[event_id] = EvidenceItem(
                    evidence_id=event_id,
                    source_type="memory_source",
                    source_id=str(evidence.get("source_id") or memory_id),
                    source_url=str(evidence.get("source_url") or ""),
                    timestamp=str(evidence.get("timestamp") or ""),
                    quote=str(evidence.get("quote") or evidence.get("content") or "")[:500],
                    reason=f"supports memory {memory_id}" if memory_id else "supports retrieved memory",
                )

        for event in self.ledger.get_events_by_ids(evidence_ids[:50]):
            event_id = str(event.get("event_id") or "").strip()
            if not event_id or event_id in items_by_id:
                continue
            items_by_id[event_id] = EvidenceItem(
                evidence_id=event_id,
                source_type=str(event.get("source_type") or "raw_event"),
                source_id=str(event.get("source_id") or event_id),
                actor_id=str(event.get("actor_id") or ""),
                timestamp=str(event.get("valid_time_start") or ""),
                quote=str(event.get("content") or "")[:500],
                reason="source event used by extracted candidate",
            )

        if not items_by_id:
            for event in self.ledger.get_events_by_project(project_id=project_id, limit=20):
                event_id = str(event.get("event_id") or "").strip()
                if not event_id:
                    continue
                items_by_id[event_id] = EvidenceItem(
                    evidence_id=event_id,
                    source_type=str(event.get("source_type") or "raw_event"),
                    source_id=str(event.get("source_id") or event_id),
                    actor_id=str(event.get("actor_id") or ""),
                    timestamp=str(event.get("valid_time_start") or ""),
                    quote=str(event.get("content") or "")[:500],
                    reason="fallback source event from the active project context",
                )

        candidate_ids = []
        for candidate in candidates:
            candidate_ids.extend(str(event_id) for event_id in candidate.get("evidence_event_ids", []))
        ordered_ids = _unique_preserve_order(evidence_ids + candidate_ids + list(items_by_id.keys()))
        ordered_items = [items_by_id[event_id] for event_id in ordered_ids if event_id in items_by_id]

        coverage = {
            "has_decision": bool(decisions or structured.get("decision_items")),
            "has_reason": bool(structured.get("reasons") or any(item.get("reason") for item in structured.get("decision_items", []))),
            "has_objection": bool(structured.get("objections") or any(item.get("objection") for item in structured.get("decision_items", []))),
            "has_conclusion": bool(structured.get("conclusions") or any(item.get("conclusion") for item in structured.get("decision_items", []))),
            "has_timeline": bool(structured.get("time_points")),
            "has_source_quote": bool(ordered_items),
        }

        return EvidenceChain(
            project_id=project_id,
            source_scope=structured.get("source_scope", ""),
            evidence_message_ids=ordered_ids[:50],
            evidence_items=ordered_items[:20],
            coverage=coverage,
        )

    def _normalize_agent_lifecycle_decision(
        self,
        decision: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Validate OpenClaw's lifecycle judgment before putting it on a card."""
        if not decision:
            return None
        allowed = {"new", "update", "conflict", "duplicate"}
        status = str(decision.get("status", "")).strip().lower()
        if status not in allowed:
            return None
        return {
            "status": status,
            "reason": str(decision.get("reason", "")).strip(),
            "target_memory_id": str(decision.get("target_memory_id", "")).strip(),
            "recommended_action": str(decision.get("recommended_action", "")).strip(),
            "confidence": float(decision.get("confidence", 0.0) or 0.0),
        }

    def _normalize_agent_structured_card(
        self,
        card: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Normalize OpenClaw's structured decision extraction."""
        card = card if isinstance(card, dict) else {}

        def list_of_text(*names: str) -> List[str]:
            values: List[str] = []
            for name in names:
                raw = card.get(name, [])
                if isinstance(raw, str):
                    raw = [raw]
                if not isinstance(raw, list):
                    continue
                for item in raw:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("content") or item.get("summary") or item.get("decision")
                    else:
                        text = item
                    text = str(text or "").strip()
                    if text and text not in values:
                        values.append(text)
            return values[:12]

        decision_items = []
        raw_items = card.get("decision_items", card.get("decisions", []))
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        if isinstance(raw_items, list):
            for item in raw_items[:12]:
                if isinstance(item, dict):
                    decision_items.append({
                        "decision": str(item.get("decision", "") or item.get("text", "") or item.get("content", "")).strip(),
                        "reason": str(item.get("reason", "") or item.get("rationale", "")).strip(),
                        "objection": str(item.get("objection", "") or item.get("opposing_view", "")).strip(),
                        "conclusion": str(item.get("conclusion", "") or item.get("outcome", "")).strip(),
                        "phase": str(item.get("phase", "")).strip(),
                        "time_point": str(item.get("time_point", "") or item.get("date", "")).strip(),
                    })
                else:
                    text = str(item or "").strip()
                    if text:
                        decision_items.append({
                            "decision": text,
                            "reason": "",
                            "objection": "",
                            "conclusion": "",
                            "phase": "",
                            "time_point": "",
                        })

        return {
            "decision_items": decision_items,
            "reasons": list_of_text("reasons", "rationale"),
            "objections": list_of_text("objections", "opposing_views"),
            "conclusions": list_of_text("conclusions", "outcomes"),
            "project_phase": str(card.get("project_phase", "") or card.get("phase", "")).strip(),
            "time_points": list_of_text("time_points", "dates"),
            "topic_links": list_of_text("topic_links", "related_topics"),
            "source_scope": str(card.get("source_scope", "") or card.get("scope", "")).strip(),
        }

    def _split_agent_summary(self, agent_summary: str) -> List[str]:
        """Split an OpenClaw-generated structured summary into card items."""
        items: List[str] = []
        for raw_line in agent_summary.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            line = re.sub(r"^[-*•\d.、\s]+", "", line).strip()
            if len(line) < 6:
                continue
            if any(keyword in line for keyword in ["决策", "行动", "风险", "周期", "模块", "结论", "待确认"]):
                if line not in items:
                    items.append(line)
        if not items and agent_summary.strip():
            items.append(agent_summary.strip()[:1200])
        return items[:12]

    def mars_update_memory(
        self,
        memory_id: str,
        updates: Dict[str, Any],
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update an existing memory.

        Args:
            memory_id: Memory ID to update.
            updates: Dict of fields to update.
            reason: Reason for update.

        Returns:
            Dict with memory_id, updated_fields.
        """
        # Get current memory
        memory = self.memory_store.get_memory(memory_id)
        if not memory:
            raise ValueError(f"Memory not found: {memory_id}")

        # Track updated fields
        updated_fields = []

        # For P0, we support status and importance updates
        if "status" in updates:
            self.memory_store.update_memory_status(memory_id, updates["status"])
            updated_fields.append("status")

        if "importance" in updates:
            # Need to implement this in memory_store
            updated_fields.append("importance")

        if "valid_time_end" in updates:
            # Need to implement this in memory_store
            updated_fields.append("valid_time_end")

        return {
            "memory_id": memory_id,
            "updated_fields": updated_fields,
            "reason": reason or "Memory updated",
        }

    def mars_reconcile(
        self,
        project_id: str,
        new_statement: str,
        context_event_ids: Optional[List[str]] = None,
        auto_resolve: bool = False,
    ) -> Dict[str, Any]:
        """Reconcile a new statement with existing memories.

        Args:
            project_id: Project ID.
            new_statement: New statement content.
            context_event_ids: Optional related event IDs.
            auto_resolve: Whether to auto-resolve conflicts.

        Returns:
            Dict with relation, old_memory_id, new_memory_id, etc.
        """
        return self.reconciler.reconcile_statement(
            project_id=project_id,
            new_statement=new_statement,
            context_event_ids=context_event_ids,
            auto_resolve=auto_resolve,
        )

    def run_auto_reconcile(
        self,
        project_id: str,
    ) -> List[Dict[str, Any]]:
        """Run automatic reconciliation for a project.

        Finds candidates that indicate supersede relationships and applies them.

        Args:
            project_id: Project ID.

        Returns:
            List of applied supersede relationships.
        """
        return auto_reconcile_updates(project_id, self.memory_store)

    def get_project_stats(
        self,
        project_id: str,
    ) -> Dict[str, Any]:
        """Get statistics for a project.

        Args:
            project_id: Project ID.

        Returns:
            Dict with various statistics.
        """
        # Count events
        event_count = self.ledger.get_event_count(project_id)

        # Count active memories
        active_memories = self.memory_store.get_active_memories(project_id)
        memory_count = len(active_memories)

        # Count candidates
        candidates = self.memory_store.get_candidates(project_id)
        candidate_count = len(candidates)

        return {
            "project_id": project_id,
            "event_count": event_count,
            "memory_count": memory_count,
            "candidate_count": candidate_count,
        }

    def get_retrieval_logs(
        self,
        project_id: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Return recent retrieval audit logs."""
        return self.memory_store.list_retrieval_logs(project_id=project_id, limit=limit)


def get_service(db_path: Optional[str] = None) -> MarsService:
    """Get the global service instance.

    Args:
        db_path: Optional database path.

    Returns:
        MarsService instance.
    """
    return MarsService(db_path)


def _unique_preserve_order(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
