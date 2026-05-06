"""
Provider boundary for model-backed reasoning.

The default implementation is deterministic. Runtime integrations can swap it
for an OpenClaw, DeepSeek, GLM, or other provider without changing MARS core
data contracts.
"""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol

from ..models import DecisionCard, EvaluationResult, LifecycleDecision


class LLMProvider(Protocol):
    """Interface for model-backed MARS reasoning."""

    def extract_memories(
        self,
        events: List[Dict[str, Any]],
        hints: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Extract memory candidates from raw events."""

    def judge_relation(
        self,
        new_memory: Dict[str, Any],
        existing_memories: List[Dict[str, Any]],
    ) -> LifecycleDecision:
        """Choose whether a memory should be new, updated, or reviewed."""

    def generate_decision_card(self, context: Dict[str, Any]) -> DecisionCard:
        """Generate a decision card from assembled context."""

    def evaluate_card(
        self,
        generated: Dict[str, Any],
        reference: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        """Score a generated card against an optional reference."""

    def plan_query(
        self,
        query: str,
        candidate_topics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Produce optional model-assisted retrieval hints."""

    def judge_consolidation(
        self,
        primary: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Judge a pair of memory records for consolidation."""


class MockLLMProvider:
    """Deterministic provider used for tests and offline operation."""

    name = "mock"

    def extract_memories(
        self,
        events: List[Dict[str, Any]],
        hints: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        memories: List[Dict[str, Any]] = []
        for index, event in enumerate(events):
            text = str(event.get("text") or event.get("content") or "").strip()
            if not text:
                continue
            memories.append({
                "candidate_id": str(event.get("event_id") or f"mock-{index}"),
                "summary": text[:160],
                "memory_type": "decision" if _looks_decisive(text) else "fact",
                "confidence": 0.5,
                "evidence_event_ids": [str(event.get("event_id") or f"mock-{index}")],
            })
        return memories

    def judge_relation(
        self,
        new_memory: Dict[str, Any],
        existing_memories: List[Dict[str, Any]],
    ) -> LifecycleDecision:
        text = _memory_text(new_memory).lower()
        for memory in existing_memories:
            existing_text = _memory_text(memory).lower()
            if text and text == existing_text:
                return LifecycleDecision(
                    status="duplicate",
                    reason="exact_text_match",
                    target_memory_id=str(memory.get("memory_id") or memory.get("id") or ""),
                    recommended_action="review_existing",
                    confidence=0.8,
                )
        return LifecycleDecision(
            status="new",
            reason="no_model_provider_configured",
            recommended_action="create_new",
            confidence=0.4,
        )

    def generate_decision_card(self, context: Dict[str, Any]) -> DecisionCard:
        return DecisionCard.model_validate(context)

    def evaluate_card(
        self,
        generated: Dict[str, Any],
        reference: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        decisions = generated.get("decisions") or generated.get("decision_items") or []
        evidence = generated.get("evidence_message_ids") or []
        dimensions = {
            "decision_coverage": 1.0 if decisions else 0.0,
            "evidence_chain": 1.0 if evidence else 0.0,
            "reference_available": 1.0 if reference else 0.0,
        }
        score = (dimensions["decision_coverage"] * 0.6) + (dimensions["evidence_chain"] * 0.4)
        return EvaluationResult(
            score=score,
            passed=score >= 0.6,
            dimensions=dimensions,
            comments=["mock_provider_score"],
        )

    def plan_query(
        self,
        query: str,
        candidate_topics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "query": query,
            "candidate_topics": candidate_topics or [],
            "provider": self.name,
            "hints": [],
        }

    def judge_consolidation(
        self,
        primary: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        relation = self.judge_relation(candidate, [primary])
        return {
            "relation": relation.status,
            "confidence": relation.confidence,
            "reason": relation.reason,
            "provider": self.name,
        }


class OpenAICompatibleLLMProvider:
    """Provider for OpenAI-compatible chat completion APIs."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 45.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.fallback = MockLLMProvider()

    def extract_memories(
        self,
        events: List[Dict[str, Any]],
        hints: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        payload = {
            "events": events[:80],
            "hints": hints or {},
        }
        result = self._chat_json(
            "Extract durable project memories. Return JSON with key memories, a list of objects with summary, memory_type, confidence, evidence_event_ids.",
            payload,
        )
        memories = result.get("memories")
        if isinstance(memories, list):
            return [item for item in memories if isinstance(item, dict)]
        return self.fallback.extract_memories(events, hints)

    def judge_relation(
        self,
        new_memory: Dict[str, Any],
        existing_memories: List[Dict[str, Any]],
    ) -> LifecycleDecision:
        result = self._chat_json(
            "Decide whether the new memory is new, update, conflict, or duplicate. Return JSON with status, reason, target_memory_id, recommended_action, confidence.",
            {
                "new_memory": new_memory,
                "existing_memories": existing_memories[:20],
                "allowed_status": ["new", "update", "conflict", "duplicate"],
            },
        )
        try:
            return LifecycleDecision.model_validate(result)
        except Exception:
            return self.fallback.judge_relation(new_memory, existing_memories)

    def generate_decision_card(self, context: Dict[str, Any]) -> DecisionCard:
        result = self._chat_json(
            "Generate a structured decision card. Return JSON matching fields title, summary, decisions, decision_items, reasons, objections, conclusions, project_phase, time_points, topic_links, source_scope, action_items, open_questions, confidence, lifecycle.",
            context,
        )
        try:
            merged = {**context, **result}
            return DecisionCard.model_validate(merged)
        except Exception:
            return self.fallback.generate_decision_card(context)

    def evaluate_card(
        self,
        generated: Dict[str, Any],
        reference: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        result = self._chat_json(
            "Evaluate the generated decision card. Return JSON with score from 0 to 1, passed, dimensions object, comments list.",
            {
                "generated": generated,
                "reference": reference or {},
                "rubric": [
                    "decision completeness",
                    "reason accuracy",
                    "objection preservation",
                    "timeline correctness",
                    "evidence traceability",
                    "lifecycle judgment",
                ],
            },
        )
        try:
            return EvaluationResult.model_validate(result)
        except Exception:
            return self.fallback.evaluate_card(generated, reference)

    def plan_query(
        self,
        query: str,
        candidate_topics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return self._chat_json(
            "Plan memory retrieval. Return JSON with query, rewritten_queries, candidate_topics, preferred_types, strict_topic.",
            {
                "query": query,
                "candidate_topics": candidate_topics or [],
            },
        )

    def judge_consolidation(
        self,
        primary: Dict[str, Any],
        candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = self._chat_json(
            "Judge consolidation relation between two records. Return JSON with relation duplicate/update/conflict/support/unrelated, confidence, reason, suggested_action.",
            {
                "primary": primary,
                "candidate": candidate,
            },
        )
        if result:
            return result
        return self.fallback.judge_consolidation(primary, candidate)

    def _chat_json(self, instruction: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        request_body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are the model boundary for MARS memory. "
                        "Return only valid JSON. Do not include markdown."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {"instruction": instruction, "payload": payload},
                        ensure_ascii=False,
                    ),
                },
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ValueError):
            return {}

        try:
            decoded = json.loads(raw)
            content = decoded["choices"][0]["message"]["content"]
            if isinstance(content, dict):
                return content
            return _loads_json_object(str(content))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return {}


def get_llm_provider(name: Optional[str] = None) -> LLMProvider:
    """Return the configured provider.

    Only the deterministic provider is bundled here. Hosted model providers
    should be added behind this function so business code does not depend on
    provider-specific SDKs or response formats.
    """

    provider_name = (name or os.getenv("MARS_LLM_PROVIDER") or "mock").strip().lower()
    if provider_name in ("", "mock", "offline"):
        return MockLLMProvider()
    if provider_name in ("openai-compatible", "openai_compatible", "deepseek", "openai"):
        api_key = (
            os.getenv("MARS_LLM_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base_url = (
            os.getenv("MARS_LLM_BASE_URL")
            or os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or ("https://api.deepseek.com" if provider_name == "deepseek" else "")
        )
        model = (
            os.getenv("MARS_LLM_MODEL")
            or os.getenv("DEEPSEEK_MODEL")
            or os.getenv("OPENAI_MODEL")
            or ("deepseek-chat" if provider_name == "deepseek" else "gpt-4o-mini")
        )
        if api_key and base_url:
            return OpenAICompatibleLLMProvider(api_key=api_key, base_url=base_url, model=model)
    return MockLLMProvider()


def _looks_decisive(text: str) -> bool:
    markers = (
        "decide", "decision", "choose", "deadline", "owner",
        "\u51b3\u5b9a", "\u51b3\u7b56", "\u786e\u5b9a", "\u622a\u6b62",
        "\u8d1f\u8d23", "\u91c7\u7528",
    )
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _memory_text(memory: Dict[str, Any]) -> str:
    return " ".join([
        str(memory.get("title") or ""),
        str(memory.get("summary") or ""),
        str(memory.get("content") or ""),
    ]).strip()


def _loads_json_object(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return {}
        parsed = json.loads(match.group(0))
    return parsed if isinstance(parsed, dict) else {}
