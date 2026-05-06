"""
Local quality benchmarks for the MARS memory pipeline.
"""

import csv
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .core.consolidator import MemoryConsolidator
from .service import MarsService
from .storage.db import init_db


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = PROJECT_ROOT / "reports"


def run_quality_benchmark(output_dir: str | None = None) -> Dict[str, Any]:
    """Run a compact end-to-end benchmark suite and write reports."""
    results = [
        _run_mixed_topic_case(),
        _run_anti_noise_case(),
        _run_query_plan_case(),
    ]
    return _write_report("benchmark", results, output_dir)


def run_consolidation_eval(output_dir: str | None = None) -> Dict[str, Any]:
    """Evaluate duplicate/update/conflict/support proposal quality."""
    cases = _consolidation_cases()
    consolidator = MemoryConsolidator()
    results = []
    for case in cases:
        actual = consolidator.propose(case["items"], max_proposals=20)
        relations = {item["relation"] for item in actual["proposals"]}
        expected = set(case["expected_relations"])
        passed = expected.issubset(relations)
        results.append({
            "benchmark_type": "consolidation",
            "case_id": case["case_id"],
            "passed": passed,
            "metric": {
                "expected_relations": sorted(expected),
                "actual_relations": sorted(relations),
                "proposal_count": actual["proposal_count"],
                "summary": actual["summary"],
            },
        })
    return _write_report("consolidation_eval", results, output_dir)


def _run_mixed_topic_case() -> Dict[str, Any]:
    service, temp_dir = _fresh_service()
    try:
        service.mars_ingest_messages(
            project_id="bench_mixed",
            messages=[
                _msg("m1", "u1", "We decided to use OpenClaw plugin architecture for memory cards.", "2026-05-01T10:00:00+08:00"),
                _msg("m2", "u2", "The MARS engine architecture keeps evidence ids for every decision.", "2026-05-01T10:01:00+08:00"),
                _msg("m3", "u1", "Deadline is Friday noon and the release schedule is tight.", "2026-05-01T10:02:00+08:00"),
                _msg("m4", "u2", "Next milestone should be before the May release date.", "2026-05-01T10:03:00+08:00"),
            ],
        )
        result = service.mars_digest("bench_mixed", auto_commit=False)
        topics = [window.get("topic_hint") for window in result["windows"]]
        passed = len(result["windows"]) >= 2 and "architecture" in topics and "timeline" in topics
        return {
            "benchmark_type": "mixed_topic",
            "case_id": "mixed_topic_split",
            "passed": passed,
            "metric": {
                "window_count": len(result["windows"]),
                "topics": topics,
                "session_states": [item.get("topic_state") for item in result.get("session_annotations", [])],
            },
        }
    finally:
        service.db.close()
        shutil.rmtree(temp_dir)


def _run_anti_noise_case() -> Dict[str, Any]:
    service, temp_dir = _fresh_service()
    try:
        service.mars_ingest_messages(
            project_id="bench_noise",
            messages=[
                _msg("n1", "u1", "We decided to use OpenClaw plugin architecture for Feishu memory cards.", "2026-05-01T10:00:00+08:00"),
                _msg("n2", "u2", "This decision keeps evidence ids visible on every card.", "2026-05-01T10:01:00+08:00"),
                _msg("n3", "u3", "Lunch order and unrelated social chatter.", "2026-05-01T10:02:00+08:00"),
                _msg("n4", "u4", "Random note about office seats and snacks.", "2026-05-01T10:03:00+08:00"),
            ],
        )
        digest = service.mars_digest("bench_noise", auto_commit=True)
        search = service.mars_search("bench_noise", "OpenClaw evidence card architecture", top_k=3)
        answer = search.get("answer", "")
        passed = search["total_retrieved"] > 0 and "OpenClaw" in answer
        return {
            "benchmark_type": "anti_noise",
            "case_id": "decision_recall_under_noise",
            "passed": passed,
            "metric": {
                "candidate_count": len(digest["candidates"]),
                "committed_count": digest["committed_count"],
                "retrieved": search["total_retrieved"],
                "query_plan": search.get("query_plan"),
            },
        }
    finally:
        service.db.close()
        shutil.rmtree(temp_dir)


def _run_query_plan_case() -> Dict[str, Any]:
    service, temp_dir = _fresh_service()
    try:
        plan = service.query_planner.plan("What is the risk with OpenClaw evidence cards?", top_k=5)
        passed = plan.query_type == "risk_lookup" and "risk" in plan.preferred_types
        return {
            "benchmark_type": "query_plan",
            "case_id": "risk_intent",
            "passed": passed,
            "metric": plan.to_dict(),
        }
    finally:
        service.db.close()
        shutil.rmtree(temp_dir)


def _consolidation_cases() -> List[Dict[str, Any]]:
    return [
        {
            "case_id": "update_and_support",
            "expected_relations": ["update", "support"],
            "items": [
                {
                    "memory_id": "m1",
                    "memory_type": "decision",
                    "topic": "architecture",
                    "content": "We decided to use plugin architecture for Feishu memory cards.",
                    "evidence_event_ids": ["e1"],
                },
                {
                    "memory_id": "m2",
                    "memory_type": "decision",
                    "topic": "architecture",
                    "content": "Update: replace the Feishu memory card plugin with OpenClaw tool flow.",
                    "evidence_event_ids": ["e2"],
                },
                {
                    "memory_id": "m3",
                    "memory_type": "fact",
                    "topic": "architecture",
                    "content": "OpenClaw plugin architecture keeps evidence event ids on the memory card.",
                    "evidence_event_ids": ["e3"],
                },
            ],
        },
        {
            "case_id": "duplicate",
            "expected_relations": ["duplicate"],
            "items": [
                {
                    "memory_id": "d1",
                    "memory_type": "decision",
                    "topic": "timeline",
                    "content": "Submit the final demo before Friday noon.",
                    "evidence_event_ids": ["e1"],
                },
                {
                    "memory_id": "d2",
                    "memory_type": "decision",
                    "topic": "timeline",
                    "content": "Submit final demo before Friday noon.",
                    "evidence_event_ids": ["e2"],
                },
            ],
        },
    ]


def _fresh_service() -> tuple[MarsService, str]:
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "bench.db"
    db = init_db(str(db_path.resolve()), force=True)
    db.close()
    return MarsService(str(db_path.resolve())), temp_dir


def _msg(message_id: str, actor_id: str, content: str, timestamp: str) -> Dict[str, str]:
    return {
        "message_id": message_id,
        "actor_id": actor_id,
        "content": content,
        "timestamp": timestamp,
    }


def _write_report(
    report_name: str,
    results: List[Dict[str, Any]],
    output_dir: str | None,
) -> Dict[str, Any]:
    report_dir = Path(output_dir) if output_dir else REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for item in results if item["passed"])
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
    }

    md_path = report_dir / f"{report_name}.md"
    csv_path = report_dir / f"{report_name}.csv"
    json_path = report_dir / f"{report_name}.json"

    md_lines = [f"# {report_name}", ""]
    for item in results:
        md_lines.extend([
            f"## {item['benchmark_type']} / {item['case_id']}",
            f"- passed: {item['passed']}",
            f"- metric: `{json.dumps(item['metric'], ensure_ascii=False)}`",
            "",
        ])
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["benchmark_type", "case_id", "passed", "metric_json"])
        for item in results:
            writer.writerow([
                item["benchmark_type"],
                item["case_id"],
                int(item["passed"]),
                json.dumps(item["metric"], ensure_ascii=False),
            ])

    json_path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        **summary,
        "results": results,
        "report_md": str(md_path),
        "report_csv": str(csv_path),
        "report_json": str(json_path),
    }
