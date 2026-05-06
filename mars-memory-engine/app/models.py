"""
Typed contracts for MARS memory outputs.

These models keep the public payloads stable while the extraction and
reasoning implementation can keep changing behind them.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


LifecycleStatus = Literal["new", "update", "conflict", "duplicate"]
RelationType = Literal["duplicate", "update", "conflict", "support", "unrelated"]


def _clamp_confidence(value: Any) -> float:
    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


class EvidenceItem(BaseModel):
    """One traceable source snippet or source reference."""

    evidence_id: str = ""
    source_type: str = "unknown"
    source_id: str = ""
    source_url: str = ""
    actor_id: str = ""
    timestamp: str = ""
    quote: str = ""
    reason: str = ""


class EvidenceChain(BaseModel):
    """Evidence bundle attached to a generated card or proposal."""

    source_id: str = ""
    source_url: str = ""
    source_title: str = ""
    source_scope: str = ""
    project_id: str = ""
    evidence_message_ids: List[str] = Field(default_factory=list)
    evidence_items: List[EvidenceItem] = Field(default_factory=list)
    coverage: Dict[str, bool] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class DecisionItem(BaseModel):
    """A structured decision with rationale and lifecycle context."""

    decision: str = ""
    reason: str = ""
    objection: str = ""
    conclusion: str = ""
    phase: str = ""
    time_point: str = ""


class LifecycleDecision(BaseModel):
    """OpenClaw or LLM judgment for card lifecycle handling."""

    status: LifecycleStatus = "new"
    reason: str = ""
    target_memory_id: str = ""
    recommended_action: str = ""
    confidence: float = 0.0

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: Any) -> float:
        return _clamp_confidence(value)


class DecisionLifecycle(BaseModel):
    """Lifecycle state for a decision card."""

    status: LifecycleStatus = "new"
    heuristic_status: LifecycleStatus = "new"
    agent_decision: Optional[LifecycleDecision] = None
    similar_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    recommended_action: str = "create_new"
    requires_confirmation: bool = True


class DecisionCard(BaseModel):
    """Public decision card payload returned by MARS tools."""

    model_config = ConfigDict(extra="allow")

    title: str = "Decision card"
    summary: List[str] = Field(default_factory=list)
    decisions: List[str] = Field(default_factory=list)
    decision_items: List[DecisionItem] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    objections: List[str] = Field(default_factory=list)
    conclusions: List[str] = Field(default_factory=list)
    project_phase: str = ""
    time_points: List[str] = Field(default_factory=list)
    topic_links: List[str] = Field(default_factory=list)
    source_scope: str = ""
    action_items: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    evidence_message_ids: List[str] = Field(default_factory=list)
    participants: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    project_id: str = ""
    query: str = ""
    evidence_chain: Optional[EvidenceChain] = None
    lifecycle: DecisionLifecycle = Field(default_factory=DecisionLifecycle)

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: Any) -> float:
        return _clamp_confidence(value)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class ConsolidationProposal(BaseModel):
    """Typed relation proposal between two memory-like records."""

    left_id: str = ""
    right_id: str = ""
    relation: RelationType = "unrelated"
    confidence: float = 0.0
    reason: str = ""
    left_topic: str = ""
    right_topic: str = ""
    suggested_action: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: Any) -> float:
        return _clamp_confidence(value)


class QueryPlanModel(BaseModel):
    """Typed representation of a retrieval plan."""

    query: str = ""
    normalized_topic: Optional[str] = None
    query_type: str = "general"
    top_k: int = 5
    preferred_types: List[str] = Field(default_factory=list)
    primary_types: List[str] = Field(default_factory=list)
    strict_topic: bool = False


class EvaluationResult(BaseModel):
    """Scoring output for a generated card."""

    score: float = 0.0
    passed: bool = False
    dimensions: Dict[str, float] = Field(default_factory=dict)
    comments: List[str] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def validate_score(cls, value: Any) -> float:
        return _clamp_confidence(value)
