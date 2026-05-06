"""
Post-processing for extracted memory candidates.

This stage filters obvious noise and marks weak claims for human/OpenClaw review
before candidates are saved or committed.
"""

import hashlib
import re
from typing import Any, Dict, List, Tuple


QUESTION_RE = re.compile(
    r"[?\uFF1F]|\u662f\u4e0d\u662f|\u662f\u5426|\u600e\u4e48|\u5982\u4f55|what if|should we",
    re.IGNORECASE,
)
PROPOSAL_RE = re.compile(
    r"maybe|might|could|\u5efa\u8bae|\u8003\u8651|\u53ef\u80fd|\u5148\u8bd5|\u6682\u5b9a|proposal",
    re.IGNORECASE,
)
DECISION_RE = re.compile(
    r"\u51b3\u5b9a|\u786e\u8ba4|\u91c7\u7528|\u4f7f\u7528|\u5b9a\u4e86|agree|agreed|decided|choose|chosen",
    re.IGNORECASE,
)


TOPIC_ALIASES = {
    "architecture": [
        "\u67b6\u6784", "\u6846\u67b6", "\u63d2\u4ef6",
        "openclaw", "mars", "memory", "engine", "stack",
    ],
    "timeline": [
        "\u622a\u6b62", "\u6392\u671f", "\u65e5\u671f",
        "\u4e0a\u7ebf", "\u53d1\u5e03", "deadline", "schedule",
    ],
    "risk": [
        "\u98ce\u9669", "\u95ee\u9898", "\u963b\u585e",
        "\u51b2\u7a81", "\u53cd\u5bf9", "risk", "issue", "conflict",
    ],
    "governance": [
        "\u5361\u7247", "\u591a\u7ef4\u8868\u683c", "\u8bc1\u636e\u94fe",
        "\u6cbb\u7406", "audit", "bitable", "card",
    ],
    "ownership": [
        "\u8d1f\u8d23\u4eba", "\u8d1f\u8d23", "\u5206\u5de5",
        "owner", "assign",
    ],
}


class CandidatePostProcessor:
    """Clean up rule-extracted memory candidates."""

    def __init__(
        self,
        drop_confidence_threshold: float = 0.45,
        review_confidence_threshold: float = 0.65,
    ):
        self.drop_confidence_threshold = drop_confidence_threshold
        self.review_confidence_threshold = review_confidence_threshold

    def process_candidates(
        self,
        candidates: List[Dict[str, Any]],
        windows_by_id: Dict[str, Dict[str, Any]] | None = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Return kept and dropped candidates."""
        kept: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        seen_fingerprints = set()

        for candidate in candidates:
            item = dict(candidate)
            summary = item.get("summary", "") or ""
            confidence = float(item.get("confidence", 0.0))
            candidate_type = item.get("candidate_type", "unknown")
            fingerprint = self._fingerprint(item)

            if fingerprint in seen_fingerprints:
                item["drop_reason"] = "duplicate_candidate"
                dropped.append(item)
                continue
            seen_fingerprints.add(fingerprint)

            item["topic_normalized"] = self._normalize_topic(item.get("topic", ""), summary)
            item["governance"] = self._governance_flags(item)

            if self._should_drop(candidate_type, summary, confidence):
                item["drop_reason"] = self._drop_reason(candidate_type, summary, confidence)
                dropped.append(item)
                continue

            if confidence < self.review_confidence_threshold or QUESTION_RE.search(summary):
                item["need_human_confirm"] = True

            if PROPOSAL_RE.search(summary) and not DECISION_RE.search(summary):
                item["need_human_confirm"] = True
                item["governance"]["claim_strength"] = "proposal"

            kept.append(item)

        return kept, dropped

    def _should_drop(self, candidate_type: str, summary: str, confidence: float) -> bool:
        if confidence < self.drop_confidence_threshold:
            return True
        if len(summary.strip()) < 8:
            return True
        if candidate_type in {"episode", "preference"} and confidence < 0.65:
            return True
        if confidence < 0.55 and PROPOSAL_RE.search(summary) and not DECISION_RE.search(summary):
            return True
        return False

    def _drop_reason(self, candidate_type: str, summary: str, confidence: float) -> str:
        if confidence < self.drop_confidence_threshold:
            return "low_confidence"
        if len(summary.strip()) < 8:
            return "too_short"
        if candidate_type in {"episode", "preference"}:
            return "low_value_memory_type"
        return "weak_proposal"

    def _normalize_topic(self, topic: str, summary: str) -> str:
        text = f"{topic} {summary}".lower()
        for normalized, markers in TOPIC_ALIASES.items():
            if any(marker.lower() in text for marker in markers):
                return normalized
        return (topic or "general").strip() or "general"

    def _governance_flags(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        summary = candidate.get("summary", "") or ""
        evidence_ids = candidate.get("evidence_event_ids") or []
        return {
            "claim_strength": "decision" if DECISION_RE.search(summary) else "statement",
            "requires_review": bool(candidate.get("need_human_confirm")),
            "evidence_count": len(evidence_ids),
            "has_evidence": bool(evidence_ids),
        }

    def _fingerprint(self, candidate: Dict[str, Any]) -> str:
        basis = "|".join([
            str(candidate.get("candidate_type", "")),
            str(candidate.get("topic", "")),
            re.sub(r"\s+", " ", str(candidate.get("summary", "")).strip().lower())[:240],
        ])
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()
