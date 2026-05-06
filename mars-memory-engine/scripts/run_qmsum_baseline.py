#!/usr/bin/env python3
"""
QMSum Baseline Test Script for MARS Memory Engine.

This script tests MARS on QMSum baseline data, reporting extraction quality.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.service import MarsService
from app.storage.db import init_db


def load_index(cases_dir: Path) -> List[Dict[str, Any]]:
    """Load the QMSum baseline index.json.

    Args:
        cases_dir: Path to the qmsum_baseline directory.

    Returns:
        List of test cases.
    """
    index_path = cases_dir / "index.json"

    if not index_path.exists():
        raise FileNotFoundError(f"Index file not found: {index_path}")

    # Handle UTF-8 BOM
    with open(index_path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    return data.get("cases", [])


def load_qmsum_file(file_path: Path) -> Dict[str, Any]:
    """Load a QMSum test case file.

    Args:
        file_path: Path to the QMSum JSON file.

    Returns:
        QMSum test case data.
    """
    # Handle UTF-8 BOM
    with open(file_path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    return data


def normalize_qmsum_messages(qmsum_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize QMSum messages for MARS ingestion.

    Args:
        qmsum_data: QMSum test case data.

    Returns:
        List of normalized messages.
    """
    plugin_input = qmsum_data.get("plugin_input", {})
    chat = plugin_input.get("chat", {})
    messages = chat.get("messages", [])

    normalized = []
    for msg in messages:
        normalized.append({
            "message_id": msg.get("message_id", ""),
            "actor_id": msg.get("sender", "Unknown"),
            "content": msg.get("text", ""),
            "timestamp": msg.get("timestamp", ""),
            "message_type": msg.get("message_type", "text"),
        })

    return normalized


def extract_gold_summary_terms(gold_summary: str) -> List[str]:
    """Extract key terms from gold summary for coverage checking.

    Args:
        gold_summary: Gold summary text.

    Returns:
        List of key terms.
    """
    # Extract nouns and important words (simple heuristic)
    # Remove common words and keep content words
    common_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
        "be", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "must", "that", "which", "who",
        "what", "when", "where", "why", "how", "it", "its", "this", "these",
        "those", "about", "into", "over", "after", "so", "if", "out", "up",
    }

    # Lowercase and extract words
    words = re.findall(r"\b[a-zA-Z]{3,}\b", gold_summary.lower())

    # Filter out common words
    key_terms = [w for w in words if w not in common_words]

    # Return unique terms
    return list(set(key_terms))


def check_keyword_coverage(
    gold_summary: str,
    memories: List[Dict[str, Any]]
) -> tuple[int, int, List[str], List[str]]:
    """Check how many gold summary terms are covered by memories.

    Args:
        gold_summary: Gold summary text.
        memories: List of committed memories.

    Returns:
        Tuple of (covered_count, total_count, covered_terms, missing_terms).
    """
    gold_terms = set(extract_gold_summary_terms(gold_summary))
    if not gold_terms:
        return 0, 0, [], []

    # Build memory content
    memory_content = " ".join([
        m.get("content", "") + " " + m.get("summary", "") + " " + m.get("topic", "")
        for m in memories
    ]).lower()

    # Check coverage
    covered = []
    missing = []

    for term in gold_terms:
        if term in memory_content:
            covered.append(term)
        else:
            missing.append(term)

    return len(covered), len(gold_terms), covered, missing


def run_test_case(
    service: MarsService,
    case: Dict[str, Any],
    cases_dir: Path,
) -> Dict[str, Any]:
    """Run a single QMSum test case.

    Args:
        service: MARS service instance.
        case: Test case definition.
        cases_dir: Path to the qmsum_baseline directory.

    Returns:
        Test result dict.
    """
    case_id = case.get("case_id", "unknown")
    file_name = case.get("file", "")
    qmsum_query = case.get("qmsum_query", "")

    print(f"\n{'='*60}")
    print(f"Testing: {case_id}")
    print(f"{'='*60}")

    # Load QMSum file
    file_path = cases_dir / file_name
    qmsum_data = load_qmsum_file(file_path)

    # Get gold summary from source if available
    # Extract gold summary from the QMSum data structure
    gold_summary = ""
    if "gold_summary" in case:
        gold_summary = case["gold_summary"]
    elif "gold" in qmsum_data and "human_reference_summary" in qmsum_data["gold"]:
        gold_summary = qmsum_data["gold"]["human_reference_summary"]
    else:
        # Try to get from QMSum data structure
        # For now, use a placeholder
        gold_summary = f"Gold summary not available for {case_id}"

    # Normalize messages
    messages = normalize_qmsum_messages(qmsum_data)

    # Get project ID
    project_id = qmsum_data.get("case_id", case_id)

    # Ingest messages
    print(f"Importing {len(messages)} messages...")
    ingest_result = service.mars_ingest_messages(
        project_id=project_id,
        messages=messages,
    )

    # Digest with auto_commit
    print("Extracting candidates and auto-committing...")
    digest_result = service.mars_digest(
        project_id=project_id,
        auto_commit=True,
    )

    # Reconcile
    print("Running auto-reconcile...")
    reconcile_result = service.run_auto_reconcile(project_id)

    # Search
    print(f"Searching for: {qmsum_query[:50]}...")
    search_result = service.mars_search(
        project_id=project_id,
        query=qmsum_query,
        top_k=5,
    )

    # Get committed memories for keyword coverage
    active_memories = service.memory_store.get_active_memories(project_id)

    # Check keyword coverage
    covered_count, total_count, covered_terms, missing_terms = \
        check_keyword_coverage(gold_summary, active_memories)

    # Extract top memory summaries
    top_memories = [
        {
            "memory_id": m.get("memory_id", ""),
            "type": m.get("memory_type", ""),
            "topic": m.get("topic", ""),
            "summary": m.get("summary") or m.get("content", ""),
            "confidence": m.get("confidence", 0),
        }
        for m in search_result.get("memories", [])[:3]
    ]

    # Build result
    result = {
        "case_id": case_id,
        "messages": len(messages),
        "relevant_messages": case.get("relevant_message_count", len(messages)),
        "imported": ingest_result.get("imported_count", 0),
        "skipped": ingest_result.get("skipped_count", 0),
        "candidates": len(digest_result.get("candidates", [])),
        "committed": digest_result.get("committed_count", 0),
        "active_memories_after_reconcile": len(active_memories),
        "supersedes": len(reconcile_result),
        "search_retrieved": search_result.get("total_retrieved", 0),
        "keyword_coverage": f"{covered_count}/{total_count}",
        "covered_terms": covered_terms[:20],  # Limit output
        "missing_terms": missing_terms[:20],  # Limit output
        "top_memories": top_memories,
        "gold_summary": gold_summary,
    }

    # Print summary
    print(f"\nResults:")
    print(f"  Imported: {result['imported']}")
    print(f"  Candidates: {result['candidates']}")
    print(f"  Committed: {result['committed']}")
    print(f"  Active memories after reconcile: {result['active_memories_after_reconcile']}")
    print(f"  Supersedes: {result['supersedes']}")
    print(f"  Search retrieved: {result['search_retrieved']}")
    print(f"  Keyword coverage: {result['keyword_coverage']}")

    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run QMSum baseline tests on MARS Memory Engine."
    )
    parser.add_argument(
        "--cases-dir",
        type=str,
        default="../test/qmsum_baseline",
        help="Path to QMSum baseline directory (default: ../test/qmsum_baseline)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Path to database file (default: temporary file)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="../test/qmsum_baseline/mars_qmsum_test_report.json",
        help="Output report path (default: ../test/qmsum_baseline/mars_qmsum_test_report.json)",
    )
    parser.add_argument(
        "--case",
        type=str,
        default=None,
        help="Run only this specific case ID",
    )

    args = parser.parse_args()

    # Resolve paths
    cases_dir = Path(args.cases_dir).resolve()
    output_path = Path(args.output).resolve()

    # Load test cases
    try:
        cases = load_index(cases_dir)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    # Filter by case if specified
    if args.case:
        cases = [c for c in cases if c.get("case_id") == args.case]
        if not cases:
            print(f"Error: Case '{args.case}' not found in index")
            sys.exit(1)

    print(f"Found {len(cases)} test case(s)")

    # Create service
    if args.db_path:
        db_path = args.db_path
    else:
        # Use temporary database
        temp_dir = tempfile.mkdtemp()
        db_path = str(Path(temp_dir) / "mars_test.db")

    print(f"Using database: {db_path}")

    # Initialize database with schema
    db = init_db(db_path, force=True)

    service = MarsService(db_path=db_path)

    # Run tests
    results = []
    for case in cases:
        try:
            result = run_test_case(service, case, cases_dir)
            results.append(result)
        except Exception as e:
            print(f"\nError processing case {case.get('case_id')}: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                "case_id": case.get("case_id", "unknown"),
                "error": str(e),
            })

    # Save report
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Report saved to: {output_path}")
    print(f"{'='*60}")

    # Print summary
    total_imported = sum(r.get("imported", 0) for r in results)
    total_candidates = sum(r.get("candidates", 0) for r in results)
    total_committed = sum(r.get("committed", 0) for r in results)

    print(f"\nTotal across all cases:")
    print(f"  Imported: {total_imported}")
    print(f"  Candidates: {total_candidates}")
    print(f"  Committed: {total_committed}")


if __name__ == "__main__":
    main()
