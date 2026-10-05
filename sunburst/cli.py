from __future__ import annotations

import argparse
import json
from dataclasses import asdict
import os
from pathlib import Path

from .db import Database
from .ingest import Ingestor
from .sec import DEFAULT_SEEDS, SECClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sunburst", description="SEC comment ingestion and search")
    parser.add_argument("--db", default=os.getenv("SUNBURST_DB", "sunburst.db"), help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Discover, fetch, extract, and index SEC comments")
    ingest.add_argument("--seed", action="append", default=[], help="Additional SEC discovery seed URL")
    ingest.add_argument("--only-seeds", action="store_true", help="Use only --seed URLs, not built-in SEC indexes")
    ingest.add_argument("--max-items", type=int, default=None, help="Stop after this many queued URLs")
    ingest.add_argument("--rps", type=float, default=4.0, help="SEC requests per second (maximum 9)")
    ingest.add_argument("--user-agent", default=os.getenv("SUNBURST_USER_AGENT"), help="Declared SEC bot identity")

    search = sub.add_parser("search", help="Run a local full-text search")
    search.add_argument("query")
    search.add_argument("--file-number")
    search.add_argument("--commenter")
    search.add_argument("--letter-type")
    search.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="Show index and crawl statistics")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    db = Database(Path(args.db))

    if args.command == "ingest":
        seeds = list(args.seed)
        if not args.only_seeds:
            seeds = [*DEFAULT_SEEDS, *seeds]
        if not seeds:
            raise SystemExit("No discovery seeds supplied")
        client = SECClient(args.user_agent, requests_per_second=args.rps)
        ingestor = Ingestor(db, client)
        ingestor.seed(seeds)
        result = ingestor.run(max_items=args.max_items)
        print(json.dumps(asdict(result), indent=2))
        return

    if args.command == "search":
        rows = db.search_comments(
            args.query,
            file_number=args.file_number,
            commenter=args.commenter,
            letter_type=args.letter_type,
            limit=args.limit,
        )
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if args.command == "stats":
        print(json.dumps(db.stats(), indent=2))
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
