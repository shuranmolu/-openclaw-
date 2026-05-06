"""
CLI for MARS Memory Engine.

Command-line interface for memory operations.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .storage.db import init_db
from .service import get_service
from .benchmark import run_consolidation_eval, run_quality_benchmark

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def cmd_init_db(args) -> int:
    """Initialize database.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    db = init_db(db_path=args.db_path, force=args.force)
    version = db.get_schema_version()
    if getattr(args, "json", False):
        print(json.dumps({
            "ok": True,
            "db_path": str(db.db_path),
            "schema_version": version,
        }, ensure_ascii=False))
        return 0
    print(f"Database initialized at: {db.db_path}")
    print(f"Schema version: {version}")
    return 0


def cmd_ingest(args) -> int:
    """Ingest messages from file.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    if args.file:
        result = service.mars_ingest_from_file(args.file)
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return 0
        print(f"Imported from: {args.file}")
        print(f"  Project: {result['project_id']}")
        print(f"  Imported: {result['imported_count']} messages")
        print(f"  Skipped: {result['skipped_count']} duplicates")
    else:
        print("Error: --file is required for ingest", file=sys.stderr)
        return 1

    return 0


def cmd_ingest_text(args) -> int:
    """Ingest text content.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    # Get text from file or command line
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            print(f"Error reading file: {e}", file=sys.stderr)
            return 1
    elif args.text:
        text = args.text
    else:
        print("Error: Either --text or --file is required", file=sys.stderr)
        return 1

    if not text.strip():
        print("Error: Text content is empty", file=sys.stderr)
        return 1

    result = service.mars_ingest_text(
        project_id=args.project_id,
        text=text,
        title=args.title,
        source_id=args.source_id,
    )

    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    print(f"Text ingestion complete for project: {args.project_id}")
    print(f"  Imported: {result['imported_count']} chunks")
    print(f"  Skipped: {result['skipped_count']} duplicates")

    return 0


def cmd_digest(args) -> int:
    """Extract memories from events.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    result = service.mars_digest(
        project_id=args.project_id,
        message_count=args.message_count,
        auto_commit=args.auto_commit,
    )
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    print(f"Digest complete for project: {args.project_id}")
    print(f"  Candidates extracted: {len(result['candidates'])}")
    print(f"  Committed to memory: {result['committed_count']}")

    if args.verbose:
        for i, candidate in enumerate(result['candidates'][:5], 1):
            print(f"\n  Candidate {i}:")
            print(f"    Type: {candidate['candidate_type']}")
            print(f"    Topic: {candidate['topic']}")
            print(f"    Summary: {candidate['summary'][:80]}...")

    return 0


def cmd_search(args) -> int:
    """Search memories.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    result = service.mars_search(
        project_id=args.project_id,
        query=args.query,
        time_scope=args.time_scope,
        top_k=args.top_k,
    )

    if args.json:
        output = {
            "ok": True,
            "query": args.query,
            "total_retrieved": result['total_retrieved'],
            "latency_ms": result['latency_ms'],
            "answer": result.get("answer", ""),
            "memories": result['memories'],
        }
        print(json.dumps(output, ensure_ascii=False))
        return 0

    print(f"Search results for: {args.query}")
    print(f"  Found: {result['total_retrieved']} memories")
    print(f"  Latency: {result['latency_ms']}ms")

    if result['answer']:
        print(f"\n{result['answer']}")

    return 0


def cmd_reconcile(args) -> int:
    """Reconcile memories.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    if args.auto:
        results = service.run_auto_reconcile(args.project_id)
        if getattr(args, "json", False):
            print(json.dumps({
                "ok": True,
                "project_id": args.project_id,
                "relationships": results,
                "count": len(results),
            }, ensure_ascii=False))
            return 0
        print(f"Auto-reconcile complete for project: {args.project_id}")
        print(f"  Supersede relationships applied: {len(results)}")

        for result in results:
            print(f"\n  {result['topic']}:")
            print(f"    Old: {result['old_memory_id']}")
            print(f"    New: {result['new_memory_id']}")
    else:
        result = service.mars_reconcile(
            project_id=args.project_id,
            new_statement=args.statement,
        )
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, **result}, ensure_ascii=False))
            return 0
        print(f"Reconciliation result:")
        print(f"  Relation: {result['relation']}")
        print(f"  Reason: {result['reason']}")
        print(f"  Action: {result['action_taken']}")
        if result['old_memory_id']:
            print(f"  Old memory: {result['old_memory_id']}")

    return 0


def cmd_stats(args) -> int:
    """Show project statistics.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    stats = service.get_project_stats(args.project_id)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **stats}, ensure_ascii=False))
        return 0

    print(f"Statistics for project: {args.project_id}")
    print(f"  Raw events: {stats['event_count']}")
    print(f"  Active memories: {stats['memory_count']}")
    print(f"  Pending candidates: {stats['candidate_count']}")

    return 0


def cmd_retrieval_logs(args) -> int:
    """Show recent retrieval audit logs."""
    service = get_service(args.db_path)
    logs = service.get_retrieval_logs(
        project_id=args.project_id,
        limit=args.limit,
    )
    if getattr(args, "json", False):
        print(json.dumps({
            "ok": True,
            "count": len(logs),
            "logs": logs,
        }, ensure_ascii=False))
        return 0

    if not logs:
        print("No retrieval logs found.")
        return 0

    for item in logs:
        selected = item.get("selected_memory_ids", [])
        print(f"{item.get('created_at')} query={item.get('query')!r} latency={item.get('latency_ms')}ms")
        print(f"  log_id: {item.get('log_id')}")
        print(f"  selected: {', '.join(selected) if selected else '(none)'}")
    return 0


def cmd_consolidate(args) -> int:
    """Show advisory duplicate/update/conflict proposals."""
    service = get_service(args.db_path)
    result = service.mars_consolidate_project(
        project_id=args.project_id,
        include_candidates=not args.memories_only,
        limit=args.limit,
    )
    if getattr(args, "json", False):
        print(json.dumps({
            "ok": True,
            "project_id": args.project_id,
            **result,
        }, ensure_ascii=False))
        return 0

    print(f"Consolidation proposals for project: {args.project_id}")
    print(f"  Proposals: {result['proposal_count']}")
    print(f"  Summary: {result['summary']}")
    for proposal in result.get("proposals", [])[:10]:
        print(f"  - {proposal['relation']}: {proposal['left_id']} -> {proposal['right_id']} ({proposal['confidence']})")
    return 0


def cmd_run_benchmark(args) -> int:
    """Run local MARS quality benchmark."""
    result = run_quality_benchmark(output_dir=args.output_dir)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    print(f"Benchmark complete: {result['passed']}/{result['total']} passed")
    print(f"  Markdown: {result['report_md']}")
    print(f"  CSV: {result['report_csv']}")
    print(f"  JSON: {result['report_json']}")
    return 0


def cmd_consolidation_eval(args) -> int:
    """Run local consolidation evaluation."""
    result = run_consolidation_eval(output_dir=args.output_dir)
    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    print(f"Consolidation eval complete: {result['passed']}/{result['total']} passed")
    print(f"  Markdown: {result['report_md']}")
    print(f"  CSV: {result['report_csv']}")
    print(f"  JSON: {result['report_json']}")
    return 0


def cmd_similar_decisions(args) -> int:
    """Find similar decisions for a query.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    result = service.mars_find_similar_decisions(
        project_id=args.project_id,
        query=args.query,
        text=args.text if hasattr(args, "text") else None,
        top_k=args.top_k if hasattr(args, "top_k") else 5,
    )

    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    print(f"Similar decisions for query: {args.query}")
    print(f"  Found: {result['total_found']} similar decisions")

    for i, sim in enumerate(result.get("similar_decisions", []), 1):
        print(f"\n  {i}. [{sim['relation'].upper()}] {sim['decision'].get('title', 'N/A')}")
        print(f"     Confidence: {sim['confidence']:.2f}")
        print(f"     Reason: {sim['reason']}")
        if sim["decision"].get("content"):
            content = sim["decision"]["content"][:100]
            print(f"     Content: {content}...")

    return 0


def cmd_command(args) -> int:
    """Process a natural language command for decision memory.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    service = get_service(args.db_path)

    # Read context from file if provided
    context_text = None
    if hasattr(args, "context_file") and args.context_file:
        try:
            with open(args.context_file, "r", encoding="utf-8") as f:
                context_text = f.read()
        except Exception as e:
            print(f"Error reading context file: {e}", file=sys.stderr)
            return 1
    elif hasattr(args, "context") and args.context:
        context_text = args.context

    agent_summary = None
    if hasattr(args, "agent_summary_file") and args.agent_summary_file:
        try:
            with open(args.agent_summary_file, "r", encoding="utf-8") as f:
                agent_summary = f.read()
        except Exception as e:
            print(f"Error reading agent summary file: {e}", file=sys.stderr)
            return 1
    elif hasattr(args, "agent_summary") and args.agent_summary:
        agent_summary = args.agent_summary

    agent_lifecycle_decision = None
    if hasattr(args, "agent_lifecycle_file") and args.agent_lifecycle_file:
        try:
            with open(args.agent_lifecycle_file, "r", encoding="utf-8-sig") as f:
                agent_lifecycle_decision = json.load(f)
        except Exception as e:
            print(f"Error reading agent lifecycle file: {e}", file=sys.stderr)
            return 1
    elif hasattr(args, "agent_lifecycle_json") and args.agent_lifecycle_json:
        try:
            agent_lifecycle_decision = json.loads(args.agent_lifecycle_json)
        except json.JSONDecodeError as e:
            print(f"Error parsing agent lifecycle JSON: {e}", file=sys.stderr)
            return 1

    agent_structured_card = None
    if hasattr(args, "agent_structured_card_file") and args.agent_structured_card_file:
        try:
            with open(args.agent_structured_card_file, "r", encoding="utf-8-sig") as f:
                agent_structured_card = json.load(f)
        except Exception as e:
            print(f"Error reading agent structured card file: {e}", file=sys.stderr)
            return 1
    elif hasattr(args, "agent_structured_card_json") and args.agent_structured_card_json:
        try:
            agent_structured_card = json.loads(args.agent_structured_card_json)
        except json.JSONDecodeError as e:
            print(f"Error parsing agent structured card JSON: {e}", file=sys.stderr)
            return 1

    result = service.mars_process_command(
        project_id=args.project_id,
        command_text=args.command,
        context_text=context_text,
        title=args.title if hasattr(args, "title") else None,
        source_id=args.source_id if hasattr(args, "source_id") else None,
        query=args.query if hasattr(args, "query") else None,
        agent_summary=agent_summary,
        agent_lifecycle_decision=agent_lifecycle_decision,
        agent_structured_card=agent_structured_card,
        auto_commit=args.auto_commit if hasattr(args, "auto_commit") else False,
    )

    if getattr(args, "json", False):
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0

    card = result.get("decision_card", {})
    lifecycle = card.get("lifecycle", {})

    print(f"Decision Card: {card.get('title', 'N/A')}")
    print(f"  Status: {lifecycle.get('status', 'unknown')}")
    print(f"  Recommended Action: {lifecycle.get('recommended_action', 'none')}")
    print(f"  Requires Confirmation: {lifecycle.get('requires_confirmation', False)}")

    similar_decisions = result.get("similar_decisions", [])
    if similar_decisions:
        print(f"\n  Similar Decisions ({len(similar_decisions)}):")
        for i, sim in enumerate(similar_decisions[:3], 1):
            print(f"    {i}. [{sim['relation']}] {sim['decision'].get('title', 'N/A')}")

    if card.get("decisions"):
        print(f"\n  Decisions:")
        for i, dec in enumerate(card["decisions"][:3], 1):
            print(f"    {i}. {dec[:80]}...")

    return 0


def cmd_run_demo(args) -> int:
    """Run the demo pipeline.

    Args:
        args: Parsed arguments.

    Returns:
        Exit code.
    """
    print("=" * 60)
    print("MARS Memory Engine - Demo Pipeline")
    print("=" * 60)

    service = get_service()

    # Step 1: Initialize database
    print("\n[1/6] Initializing database...")
    db = init_db(force=True)
    print(f"  [OK] Database initialized at: {db.db_path}")

    # Step 2: Create sample data if needed
    sample_file = Path("data/sample_chats/carbon_platform.json")
    if not sample_file.exists():
        print("\n[2/6] Creating sample data...")
        from .connectors.sample_loader import create_carbon_platform_sample
        create_carbon_platform_sample(str(sample_file))
        print(f"  [OK] Sample data created at: {sample_file}")
    else:
        print("\n[2/6] Using existing sample data...")
        print(f"  [OK] Sample data found at: {sample_file}")

    # Step 3: Ingest messages
    print("\n[3/6] Ingesting messages...")
    result = service.mars_ingest_from_file(str(sample_file))
    print(f"  [OK] Project: {result['project_id']}")
    print(f"  [OK] Imported: {result['imported_count']} messages")
    print(f"  [OK] Skipped: {result['skipped_count']} duplicates")

    # Step 4: Extract memories
    print("\n[4/6] Extracting memories...")
    digest_result = service.mars_digest(
        project_id=result['project_id'],
        auto_commit=True,
    )
    print(f"  [OK] Candidates extracted: {len(digest_result['candidates'])}")
    print(f"  [OK] Committed to memory: {digest_result['committed_count']}")

    # Show some memories
    print("\n  Extracted memories:")
    for i, candidate in enumerate(digest_result['candidates'][:3], 1):
        print(f"    {i}. [{candidate['candidate_type']}] {candidate['topic']}")
        print(f"       {candidate['summary'][:60]}...")

    # Step 5: Search memories
    print("\n[5/6] Testing search...")
    search_result = service.mars_search(
        project_id=result['project_id'],
        query="技术路线",
        top_k=3,
    )
    print(f"  [OK] Search for '技术路线' found: {search_result['total_retrieved']} memories")

    # Step 6: Reconcile
    print("\n[6/6] Running auto-reconcile...")
    reconcile_results = service.run_auto_reconcile(result['project_id'])
    print(f"  [OK] Supersede relationships: {len(reconcile_results)}")

    # Show supersede results
    if reconcile_results:
        print("\n  Supersede relationships applied:")
        for rel_result in reconcile_results:
            print(f"    Topic: {rel_result['topic']}")
            print(f"      Old: {rel_result['old_memory_id']} → New: {rel_result['new_memory_id']}")

    # Final stats
    print("\n" + "=" * 60)
    print("Demo Complete!")
    print("=" * 60)

    stats = service.get_project_stats(result['project_id'])
    print(f"\nFinal Statistics:")
    print(f"  Raw events: {stats['event_count']}")
    print(f"  Active memories: {stats['memory_count']}")
    print(f"  Pending candidates: {stats['candidate_count']}")

    return 0


def main() -> int:
    """Main entry point.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        description="MARS Memory Engine - Enterprise Memory Management System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--db-path",
        help="Path to SQLite database file",
    )

    subparsers = parser.add_subparsers(dest="command_name", help="Available commands")

    # init-db
    init_parser = subparsers.add_parser("init-db", help="Initialize database schema")
    init_parser.add_argument("--force", action="store_true", help="Delete and recreate database")
    init_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="Ingest messages from file")
    ingest_parser.add_argument("--file", required=True, help="Path to JSON file")
    ingest_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # ingest-text
    ingest_text_parser = subparsers.add_parser("ingest-text", help="Ingest text content")
    ingest_text_parser.add_argument("--project-id", required=True, help="Project ID")
    ingest_text_parser.add_argument("--text", help="Text content to ingest")
    ingest_text_parser.add_argument("--file", help="Path to text file (alternative to --text)")
    ingest_text_parser.add_argument("--title", help="Optional document title (for idempotency)")
    ingest_text_parser.add_argument("--source-id", help="Optional source ID (for idempotency)")
    ingest_text_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # digest
    digest_parser = subparsers.add_parser("digest", help="Extract memories from events")
    digest_parser.add_argument("--project-id", required=True, help="Project ID")
    digest_parser.add_argument("--message-count", type=int, default=100, help="Number of recent messages")
    digest_parser.add_argument("--auto-commit", action="store_true", help="Auto-commit high-confidence candidates")
    digest_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    digest_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # search
    search_parser = subparsers.add_parser("search", help="Search memories")
    search_parser.add_argument("--project-id", required=True, help="Project ID")
    search_parser.add_argument("--query", required=True, help="Search query")
    search_parser.add_argument("--time-scope", default="current", choices=["current", "all", "history"], help="Time scope")
    search_parser.add_argument("--top-k", type=int, default=5, help="Maximum results")
    search_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # reconcile
    reconcile_parser = subparsers.add_parser("reconcile", help="Reconcile memories")
    reconcile_parser.add_argument("--project-id", required=True, help="Project ID")
    reconcile_parser.add_argument("--statement", help="New statement to reconcile")
    reconcile_parser.add_argument("--auto", action="store_true", help="Run auto-reconcile")
    reconcile_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # stats
    stats_parser = subparsers.add_parser("stats", help="Show project statistics")
    stats_parser.add_argument("--project-id", required=True, help="Project ID")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # retrieval-logs
    logs_parser = subparsers.add_parser("retrieval-logs", help="Show recent retrieval audit logs")
    logs_parser.add_argument("--project-id", help="Optional project ID")
    logs_parser.add_argument("--limit", type=int, default=20, help="Maximum logs to return")
    logs_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # consolidate
    consolidate_parser = subparsers.add_parser("consolidate", help="Show duplicate/update/conflict proposals")
    consolidate_parser.add_argument("--project-id", required=True, help="Project ID")
    consolidate_parser.add_argument("--memories-only", action="store_true", help="Exclude pending candidates")
    consolidate_parser.add_argument("--limit", type=int, default=50, help="Maximum proposals to return")
    consolidate_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # run-benchmark
    benchmark_parser = subparsers.add_parser("run-benchmark", help="Run local quality benchmark")
    benchmark_parser.add_argument("--output-dir", help="Optional report output directory")
    benchmark_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # consolidation-eval
    consolidation_eval_parser = subparsers.add_parser("consolidation-eval", help="Run consolidation evaluation")
    consolidation_eval_parser.add_argument("--output-dir", help="Optional report output directory")
    consolidation_eval_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # similar-decisions
    similar_parser = subparsers.add_parser("similar-decisions", help="Find similar decisions")
    similar_parser.add_argument("--project-id", required=True, help="Project ID")
    similar_parser.add_argument("--query", required=True, help="Search query")
    similar_parser.add_argument("--text", help="Optional full text for comparison")
    similar_parser.add_argument("--top-k", type=int, default=5, help="Maximum results")
    similar_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # command
    command_parser = subparsers.add_parser("command", help="Process natural language command")
    command_parser.add_argument("--project-id", required=True, help="Project ID")
    command_parser.add_argument("--command", required=True, help="Natural language command")
    command_parser.add_argument("--context", help="Optional context text")
    command_parser.add_argument("--context-file", help="Optional context file path")
    command_parser.add_argument("--agent-summary", help="Optional OpenClaw-generated structured summary")
    command_parser.add_argument("--agent-summary-file", help="Optional file containing OpenClaw-generated structured summary")
    command_parser.add_argument("--agent-lifecycle-json", help="Optional OpenClaw lifecycle judgment as JSON")
    command_parser.add_argument("--agent-lifecycle-file", help="Optional file containing OpenClaw lifecycle judgment JSON")
    command_parser.add_argument("--agent-structured-card-json", help="Optional OpenClaw structured decision card JSON")
    command_parser.add_argument("--agent-structured-card-file", help="Optional file containing OpenClaw structured decision card JSON")
    command_parser.add_argument("--title", help="Optional title for document source")
    command_parser.add_argument("--source-id", help="Optional source ID")
    command_parser.add_argument("--query", help="Optional query for searching similar decisions")
    command_parser.add_argument("--auto-commit", action="store_true", help="Auto-commit high-confidence candidates")
    command_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # run-demo
    subparsers.add_parser("run-demo", help="Run the demo pipeline")

    args = parser.parse_args()

    if not args.command_name:
        parser.print_help()
        return 1

    # Route to command handler
    commands = {
        "init-db": cmd_init_db,
        "ingest": cmd_ingest,
        "ingest-text": cmd_ingest_text,
        "digest": cmd_digest,
        "search": cmd_search,
        "reconcile": cmd_reconcile,
        "stats": cmd_stats,
        "retrieval-logs": cmd_retrieval_logs,
        "consolidate": cmd_consolidate,
        "run-benchmark": cmd_run_benchmark,
        "consolidation-eval": cmd_consolidation_eval,
        "similar-decisions": cmd_similar_decisions,
        "command": cmd_command,
        "run-demo": cmd_run_demo,
    }

    handler = commands.get(args.command_name)
    if handler:
        return handler(args)

    return 1


if __name__ == "__main__":
    sys.exit(main())
