from __future__ import annotations

import argparse
import json
from dataclasses import asdict
import os
from pathlib import Path

from .archive import ArchiveStore
from .db import Database
from .history import HISTORICAL_SEEDS, HistoricalIngestor
from .ingest import Ingestor
from .sec import DEFAULT_SEEDS, SECClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sunburst",
        description="Ingest and search SEC comments and historical documents",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("SUNBURST_DB", "sunburst.db"),
        help="SQLite database path",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Discover, fetch, extract, and index SEC comments")
    ingest.add_argument("--seed", action="append", default=[], help="Additional SEC discovery seed URL")
    ingest.add_argument("--only-seeds", action="store_true", help="Use only --seed URLs, not built-in SEC indexes")
    ingest.add_argument("--max-items", type=int, default=None, help="Stop after this many queued URLs")
    ingest.add_argument("--rps", type=float, default=4.0, help="SEC requests per second (maximum 9)")
    ingest.add_argument("--user-agent", default=os.getenv("SUNBURST_USER_AGENT"), help="Declared SEC bot identity")

    history = sub.add_parser(
        "ingest-history",
        help="Archive SEC historical documents and index their text",
    )
    history.add_argument(
        "--seed",
        action="append",
        default=[],
        metavar="CATEGORY=URL",
        help="Additional historical SEC seed; URL alone uses category 'custom'",
    )
    history.add_argument(
        "--only-seeds",
        action="store_true",
        help="Use only --seed values, not Sunburst's built-in historical archive roots",
    )
    history.add_argument(
        "--blob-dir",
        default=os.getenv("SUNBURST_BLOB_DIR"),
        help="Directory for content-addressed original SEC files",
    )
    history.add_argument("--max-items", type=int, default=None, help="Stop after this many queued URLs")
    history.add_argument("--rps", type=float, default=4.0, help="SEC requests per second (maximum 9)")
    history.add_argument("--user-agent", default=os.getenv("SUNBURST_USER_AGENT"), help="Declared SEC bot identity")

    search = sub.add_parser("search", help="Search indexed SEC comments")
    search.add_argument("query")
    search.add_argument("--file-number")
    search.add_argument("--commenter")
    search.add_argument("--letter-type")
    search.add_argument("--limit", type=int, default=20)

    history_search = sub.add_parser(
        "search-history",
        help="Search indexed SEC historical documents",
    )
    history_search.add_argument("query")
    history_search.add_argument("--category")
    history_search.add_argument("--year", type=int)
    history_search.add_argument("--release-number")
    history_search.add_argument("--file-number")
    history_search.add_argument("--limit", type=int, default=20)

    search_all = sub.add_parser(
        "search-all",
        help="Search comments and historical documents together",
    )
    search_all.add_argument("query")
    search_all.add_argument("--limit", type=int, default=20)

    sub.add_parser("stats", help="Show comment and historical archive statistics")
    return parser


def _parse_history_seed(value: str) -> tuple[str, str, str]:
    if "=" in value:
        category, url = value.split("=", 1)
        category = category.strip()
        url = url.strip()
        if not category or not url:
            raise ValueError(f"Invalid historical seed: {value!r}")
        return category, url, "page"
    return "custom", value.strip(), "page"


def main() -> None:
    args = build_parser().parse_args()
    db = Database(Path(args.db))
    archive = ArchiveStore(db)

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

    if args.command == "ingest-history":
        custom = [_parse_history_seed(value) for value in args.seed]
        seeds = custom if args.only_seeds else [*HISTORICAL_SEEDS, *custom]
        if not seeds:
            raise SystemExit("No historical SEC seeds supplied")
        client = SECClient(args.user_agent, requests_per_second=args.rps)
        ingestor = HistoricalIngestor(
            db,
            client,
            blob_dir=args.blob_dir,
        )
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

    if args.command == "search-history":
        rows = archive.search_documents(
            args.query,
            category=args.category,
            year=args.year,
            release_number=args.release_number,
            file_number=args.file_number,
            limit=args.limit,
        )
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return

    if args.command == "search-all":
        print(
            json.dumps(
                {
                    "comments": db.search_comments(args.query, limit=args.limit),
                    "historical_documents": archive.search_documents(
                        args.query,
                        limit=args.limit,
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    if args.command == "stats":
        print(
            json.dumps(
                {
                    "comments": db.stats(),
                    "historical_documents": archive.stats(),
                },
                indent=2,
            )
        )
        return

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
