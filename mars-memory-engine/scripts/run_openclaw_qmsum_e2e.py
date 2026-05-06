"""Run QMSum -> MARS -> OpenClaw decision-card E2E test.

This script keeps the heavy extraction local in MARS, then asks OpenClaw to
format retrieved memories into a decision card JSON. It intentionally sends
only compact memory/evidence snippets to OpenClaw, not full meeting transcripts.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ENGINE_ROOT = SCRIPT_DIR.parent
REPO_ROOT = ENGINE_ROOT.parent
sys.path.insert(0, str(ENGINE_ROOT))

from app.service import get_service  # noqa: E402
from app.storage.db import init_db  # noqa: E402


STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "into", "were", "was",
    "are", "for", "about", "would", "could", "should", "there", "their",
    "they", "have", "has", "had", "been", "being", "what", "did", "group",
    "discuss", "discussion", "summarize", "because", "which", "only",
}

# Product design keywords for QMSum remote control domain
PRODUCT_KEYWORDS = {
    "remote", "control", "design", "style", "optimization", "market", "research",
    "requirements", "buttons", "menu", "display", "material", "plastic", "cost",
    "touch", "screen", "speech", "recognition", "alarm", "logo", "size",
    "user", "friendly", "complicated", "incorporated", "suggested", "recommended",
    "refused", "gave", "appliances", "industrial", "designer", "marketing",
    "interface", "lcd", "monochrome", "infrared", "circuit", "schematic",
    "international", "corporate", "integration", "lightweight", "metal",
}

# Meeting logistics/chat keywords to downweight
LOGISTICS_KEYWORDS = {
    "agenda", "meeting", "first", "meeting", "running", "blue", "space",
    "everyone", "guess", "tiger", "vocalsound", "disfmarker", "hello",
    "introduction", "introduce", "present", "presentation", "slide", "thank",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def terms(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for word in re.findall(r"[A-Za-z][A-Za-z\-]+", text.lower()):
        word = word.strip("-")
        if len(word) < 4 or word in STOPWORDS or word in seen:
            continue
        seen.add(word)
        result.append(word)
    return result


def score_memory_for_query(memory: dict[str, Any], query: str, query_terms: set[str]) -> dict[str, Any]:
    """Score a memory based on query relevance and product decision content.

    Returns a dict with:
    - score: float (0-100)
    - reason: str explaining the score
    """
    topic = str(memory.get("topic", "")).lower()
    content = str(memory.get("content", "")).lower()
    memory_type = str(memory.get("memory_type", "")).lower()
    full_text = f"{topic} {content} {memory_type}"

    # Base score from confidence
    confidence = float(memory.get("confidence", 0.5))
    score = confidence * 30

    reasons = [f"base(confidence={confidence:.2f})={score:.1f}"]

    # Query term matching (boost)
    query_matches = sum(1 for term in query_terms if term in full_text)
    if query_matches > 0:
        boost = min(query_matches * 8, 40)
        score += boost
        reasons.append(f"query_matches({query_matches})=+{boost:.1f}")

    # Product keyword matching
    product_hits = sum(1 for kw in PRODUCT_KEYWORDS if kw in full_text)
    if product_hits > 0:
        boost = min(product_hits * 3, 20)
        score += boost
        reasons.append(f"product_terms({product_hits})=+{boost:.1f}")

    # Decision-related keywords boost
    decision_terms = {"agreed", "chose", "chosen", "decided", "recommended",
                      "suggested", "refused", "rejected", "selected", "approved",
                      "final", "conclusion", "resolved", "agreement"}
    decision_hits = sum(1 for dt in decision_terms if dt in full_text)
    if decision_hits > 0:
        boost = min(decision_hits * 5, 15)
        score += boost
        reasons.append(f"decision_terms({decision_hits})=+{boost:.1f}")

    # Type boost for decision/conclusion memories
    if "decision" in memory_type or "conclusion" in memory_type:
        score += 10
        reasons.append("type_is_decision=+10")

    # Logistics/chat penalty
    logistics_hits = sum(1 for lk in LOGISTICS_KEYWORDS if lk in full_text)
    if logistics_hits > 0:
        # Heavy penalty for pure logistics content
        penalty = min(logistics_hits * 8, 30)
        score -= penalty
        reasons.append(f"logistics_terms({logistics_hits})=-{penalty:.1f}")

    # Penalty for very short content (likely low value)
    if len(content) < 100:
        score -= 5
        reasons.append("short_content=-5")

    # Clamp score to 0-100
    score = max(0, min(100, score))

    return {
        "score": round(score, 1),
        "reason": ", ".join(reasons),
    }


def parse_json_object(text: str) -> tuple[dict[str, Any] | None, str | None]:
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.S)
    if fenced:
        cleaned = fenced.group(1)
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "parsed value is not a JSON object"
    return value, None


def normalize_card(card: dict[str, Any] | None) -> dict[str, Any] | None:
    if card is None:
        return None
    normalized = {
        "title": str(card.get("title", "")),
        "summary": str(card.get("summary", "")),
        "decisions": card.get("decisions") if isinstance(card.get("decisions"), list) else [],
        "action_items": card.get("action_items") if isinstance(card.get("action_items"), list) else [],
        "open_questions": card.get("open_questions") if isinstance(card.get("open_questions"), list) else [],
        "participants": card.get("participants") if isinstance(card.get("participants"), list) else [],
        "evidence_message_ids": card.get("evidence_message_ids")
        if isinstance(card.get("evidence_message_ids"), list)
        else [],
        "confidence": card.get("confidence", 0),
    }
    try:
        normalized["confidence"] = float(normalized["confidence"])
    except (TypeError, ValueError):
        normalized["confidence"] = 0.0
    return normalized


def compact_sources(memories: list[dict[str, Any]], limit: int = 12,
                    query: str = "", with_scoring: bool = False) -> list[dict[str, Any]]:
    """Compact memories for OpenClaw consumption, optionally with query-aware scoring.

    Args:
        memories: List of memory dicts to compact
        limit: Max number of memories to return
        query: Query string for scoring (if with_scoring=True)
        with_scoring: If True, score and sort by query relevance
    """
    if with_scoring and query:
        query_terms = set(terms(query))
        scored_memories = []
        for memory in memories:
            score_info = score_memory_for_query(memory, query, query_terms)
            scored_memories.append((memory, score_info))
        # Sort by score descending
        scored_memories.sort(key=lambda x: x[1]["score"], reverse=True)
        memories_to_use = [m for m, _ in scored_memories[:limit]]
        scores_to_use = {m.get("memory_id"): s for m, s in scored_memories[:limit]}
    else:
        memories_to_use = memories[:limit]
        scores_to_use = {}

    compact_memories: list[dict[str, Any]] = []
    for memory in memories_to_use:
        memory_id = memory.get("memory_id")
        evidence_ids: list[str] = []
        evidence_quotes: list[str] = []
        for source in memory.get("evidence", [])[:3]:
            event_id = source.get("event_id") or ""
            if event_id:
                evidence_ids.append(event_id)
            quote = source.get("content") or source.get("quote") or ""
            if quote:
                evidence_quotes.append(quote[:220])
        compact = {
            "memory_id": memory_id,
            "type": memory.get("memory_type"),
            "topic": memory.get("topic"),
            "content": memory.get("content", "")[:500],
            "confidence": memory.get("confidence"),
            "evidence_message_ids": evidence_ids,
            "evidence_quotes": evidence_quotes,
        }
        if with_scoring and memory_id in scores_to_use:
            compact["score"] = scores_to_use[memory_id]["score"]
            compact["score_reason"] = scores_to_use[memory_id]["reason"]
        compact_memories.append(compact)
    return compact_memories


def build_openclaw_prompt(case_id: str, query: str, source_file: Path) -> str:
    """Build OpenClaw prompt for generating PRODUCT DECISION CARD from sources.

    Key emphasis: This is NOT a meeting summary. It's a PRODUCT DECISION CARD.
    Focus on concrete product decisions, not process or "further research needed".
    """

    # Use absolute path for Windows compatibility
    source_path = str(source_file.resolve())

    # Build prompt as a single line to avoid newline parsing issues
    prompt = (
        f"Use read tool to open file: {source_path} "
        f"Use ONLY that file's sources field. "
        f"Return strict JSON only with fields: title, summary, decisions, action_items, open_questions, participants, evidence_message_ids, confidence. "
        f"action_items must be list of objects with owner, task, deadline. "
        f"TASK: Generate PRODUCT DECISION CARD not meeting minutes. "
        f"Focus on CONCRETE product decisions: features accepted/rejected (buttons, display, touch screen, speech recognition, alarm), "
        f"design choices (style, size, material plastic/metal, color, logo), "
        f"technical decisions (infrared, LCD type, circuit), "
        f"market decisions (regions, demographics), "
        f"cost decisions (what chosen due to cost, what rejected as too expensive). "
        f"DECISION RULES: 1) Write product features/materials/cost/rejected proposals as decisions. "
        f"2) Do NOT write needs further research as main decision unless no concrete decisions exist. "
        f"3) Prefer explicit agreement words: agreed, chose, decided, recommended, suggested, refused, rejected, gave up, incorporated. "
        f"4) If sources conflict, state final decision and mention uncertainty in open_questions. "
        f"IGNORE: meeting logistics, agenda, introductions, scheduling, hello, thank you, guess, running out of blue. "
        f"Use evidence_message_ids only from source file. "
        f"Case ID: {case_id}. User query: {query}."
    )
    return prompt


def run_openclaw(
    openclaw_cmd: Path,
    openclaw_workdir: Path,
    prompt: str,
    session_id: str,
    timeout: int,
) -> dict[str, Any]:
    cmd = [
        str(openclaw_cmd),
        "agent",
        "--session-id",
        session_id,
        "--message",
        prompt,
        "--json",
        "--timeout",
        str(timeout),
    ]
    started = time.time()
    proc = subprocess.run(
        cmd,
        cwd=str(openclaw_workdir),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout + 30,
    )
    duration_ms = int((time.time() - started) * 1000)
    result: dict[str, Any] = {
        "returncode": proc.returncode,
        "duration_ms": duration_ms,
        "stdout_len": len(proc.stdout),
        "stdout_preview": proc.stdout[:1000],
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    if proc.returncode != 0:
        result["error"] = f"OpenClaw exited with {proc.returncode}"
        return result
    try:
        outer = json.loads(proc.stdout)
    except json.JSONDecodeError:
        outer = None
    result["outer"] = outer
    if isinstance(outer, dict) and "result" in outer:
        payloads = outer.get("result", {}).get("payloads", [])
        text = payloads[0].get("text", "") if payloads else ""
    else:
        text = proc.stdout
    result["assistant_text"] = text
    card, parse_error = parse_json_object(text)
    result["card"] = normalize_card(card)
    result["parse_error"] = parse_error
    return result


def deterministic_judge(card: dict[str, Any] | None, gold_summary: str, memories: list[dict[str, Any]]) -> dict[str, Any]:
    if not card:
        return {
            "factual_consistency": 1,
            "coverage": 1,
            "decision_identification": 1,
            "evidence_grounding": 1,
            "card_usability": 1,
            "overall_score": 1.0,
            "major_errors": ["OpenClaw did not return parseable decision-card JSON."],
            "missing_key_points": terms(gold_summary)[:12],
            "covered_terms": [],
            "coverage_ratio": 0.0,
        }

    card_text = json.dumps(card, ensure_ascii=False).lower()
    memory_text = " ".join(m.get("content", "") for m in memories).lower()
    gold_terms = terms(gold_summary)
    covered = [term for term in gold_terms if term in card_text]
    missing = [term for term in gold_terms if term not in card_text][:12]
    grounded_decisions = [d for d in card.get("decisions", []) if str(d).lower() in memory_text or any(t in memory_text for t in terms(str(d))[:3])]

    coverage_ratio = len(covered) / max(1, len(gold_terms))
    coverage = 1 + min(4, round(coverage_ratio * 5))
    evidence_grounding = 4 if card.get("evidence_message_ids") else 2
    decision_identification = 4 if card.get("decisions") else 2
    if not grounded_decisions and card.get("decisions"):
        factual_consistency = 3
    else:
        factual_consistency = 4 if card.get("summary") else 2
    card_usability = 4 if card.get("title") and card.get("summary") and card.get("decisions") else 3
    scores = [factual_consistency, coverage, decision_identification, evidence_grounding, card_usability]
    return {
        "factual_consistency": factual_consistency,
        "coverage": coverage,
        "decision_identification": decision_identification,
        "evidence_grounding": evidence_grounding,
        "card_usability": card_usability,
        "overall_score": round(sum(scores) / len(scores), 2),
        "major_errors": [] if coverage_ratio >= 0.25 else ["Low coverage of the QMSum human reference summary."],
        "missing_key_points": missing,
        "covered_terms": covered[:12],
        "coverage_ratio": round(coverage_ratio, 3),
    }


def run_case(case: dict[str, Any], cases_dir: Path, args: argparse.Namespace, db_dir: Path) -> dict[str, Any]:
    data = read_json(cases_dir / case["file"])
    case_id = case["case_id"]
    query = data.get("gold", {}).get("qmsum_query") or case.get("qmsum_query", "")
    gold_summary = data.get("gold", {}).get("human_reference_summary", "")

    db_path = db_dir / f"{case_id}.db"
    init_db(str(db_path), force=True)
    service = get_service(str(db_path))
    try:
        ingest = service.mars_ingest_from_file(str(cases_dir / case["file"]))
        digest = service.mars_digest(project_id=ingest["project_id"], auto_commit=True)
        reconcile = service.run_auto_reconcile(ingest["project_id"])
        search = service.mars_search(project_id=ingest["project_id"], query=query, top_k=args.top_k)
        memories = search.get("memories", [])

        source_dir = args.openclaw_workdir / "workspace" / "mars_e2e_sources"
        source_dir.mkdir(parents=True, exist_ok=True)
        active_memories = service.memory_store.get_active_memories(ingest["project_id"])
        source_file = source_dir / f"{case_id}.json"

        # Use query-aware scoring for source selection
        scored_sources = compact_sources(active_memories, limit=args.source_limit,
                                          query=query, with_scoring=True)
        write_json(
            source_file,
            {
                "case_id": case_id,
                "qmsum_query": query,
                "retrieved_sources": compact_sources(memories, limit=args.top_k),
                "sources": scored_sources,
            },
        )

        openclaw_result = run_openclaw(
            args.openclaw_cmd,
            args.openclaw_workdir,
            build_openclaw_prompt(case_id, query, source_file),
            f"mars-qmsum-{case_id}-{int(time.time())}",
            args.timeout,
        )
        judge = deterministic_judge(openclaw_result.get("card"), gold_summary, memories)

        # Include source selection details in report for review
        selected_sources_summary = [
            {
                "topic": s.get("topic", "")[:80],
                "type": s.get("type", ""),
                "score": s.get("score", 0),
                "reason": s.get("score_reason", ""),
            }
            for s in scored_sources[:8]  # Top 8 for review
        ]

        return {
            "case_id": case_id,
            "qmsum_query": query,
            "gold_summary": gold_summary,
            "mars": {
                "imported": ingest.get("imported_count", 0),
                "candidates": len(digest.get("candidates", [])),
                "committed": digest.get("committed_count", 0),
                "supersedes": len(reconcile),
                "search_retrieved": search.get("total_retrieved", 0),
            },
            "source_selection": {
                "total_active": len(active_memories),
                "selected_count": len(scored_sources),
                "top_sources": selected_sources_summary,
            },
            "openclaw": {
                "ok": openclaw_result.get("returncode") == 0 and openclaw_result.get("card") is not None,
                "source_file": str(source_file),
                "duration_ms": openclaw_result.get("duration_ms"),
                "error": openclaw_result.get("error") or openclaw_result.get("parse_error"),
                "assistant_text": openclaw_result.get("assistant_text"),
                "stdout_len": openclaw_result.get("stdout_len"),
                "stdout_preview": openclaw_result.get("stdout_preview"),
                "stderr": openclaw_result.get("stderr"),
                "card": openclaw_result.get("card"),
            },
            "judge": judge,
        }
    finally:
        service.db.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases-dir", type=Path, default=REPO_ROOT / "test" / "qmsum_baseline")
    parser.add_argument("--openclaw-cmd", type=Path, required=True)
    parser.add_argument("--openclaw-workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--source-limit", type=int, default=12)
    args = parser.parse_args()

    cases_dir = args.cases_dir.resolve()
    output = args.output or (cases_dir / "openclaw_qmsum_e2e_report.json")
    index = read_json(cases_dir / "index.json")
    cases = index.get("cases", [])
    results: list[dict[str, Any]] = []

    db_dir = Path(tempfile.mkdtemp(prefix="mars_openclaw_e2e_"))
    for case in cases:
        print(f"Running E2E case: {case['case_id']}")
        result = run_case(case, cases_dir, args, db_dir)
        results.append(result)
        mars = result["mars"]
        judge = result["judge"]
        print(
            f"  MARS candidates={mars['candidates']} committed={mars['committed']} "
            f"search={mars['search_retrieved']} OpenClaw ok={result['openclaw']['ok']} "
            f"judge={judge['overall_score']}"
        )

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pipeline": "QMSum baseline -> MARS local engine -> OpenClaw agent decision card -> local judge",
        "openclaw_cmd": str(args.openclaw_cmd),
        "cases": results,
    }
    write_json(output, report)
    print(f"Report saved to: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
