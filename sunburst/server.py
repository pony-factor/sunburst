from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .archive import ArchiveStore
from .db import Database

mcp = MCPServer("sunburst")


def _database() -> Database:
    return Database(Path(os.getenv("SUNBURST_DB", "sunburst.db")))


def _archive() -> ArchiveStore:
    return ArchiveStore(_database())


@mcp.tool()
def search_comments(
    query: str,
    file_number: str | None = None,
    commenter: str | None = None,
    letter_type: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search SEC comments with SQLite FTS5, optionally filtering by file number, commenter, or letter type."""
    return _database().search_comments(
        query,
        file_number=file_number,
        commenter=commenter,
        letter_type=letter_type,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def get_comment(comment_id: int) -> dict[str, Any] | None:
    """Return one indexed SEC comment, including its full extracted text and source URL."""
    return _database().get_comment(comment_id)


@mcp.tool()
def list_files(prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """List indexed SEC file numbers and their comment counts."""
    return _database().list_files(prefix=prefix, limit=limit)


@mcp.tool()
def search_historical_documents(
    query: str,
    category: str | None = None,
    year: int | None = None,
    release_number: str | None = None,
    file_number: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Search archived SEC reports, releases, speeches, orders, guidance, rules, and related historical documents."""
    return _archive().search_documents(
        query,
        category=category,
        year=year,
        release_number=release_number,
        file_number=file_number,
        limit=limit,
        offset=offset,
    )


@mcp.tool()
def get_historical_document(document_id: int) -> dict[str, Any] | None:
    """Return a historical SEC document with full extracted text, metadata, original URL, and stored blob path."""
    return _archive().get_document(document_id)


@mcp.tool()
def list_historical_categories() -> list[dict[str, Any]]:
    """List historical SEC document categories with counts and indexed date ranges."""
    return _archive().list_categories()


@mcp.tool()
def search_sec_corpus(
    query: str,
    limit: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Search public comments and the broader historical SEC archive in one request."""
    db = _database()
    archive = ArchiveStore(db)
    return {
        "comments": db.search_comments(query, limit=limit),
        "historical_documents": archive.search_documents(query, limit=limit),
    }


@mcp.tool()
def index_stats() -> dict[str, Any]:
    """Return comment corpus and historical archive sizes plus crawl queue status."""
    db = _database()
    return {
        "comments": db.stats(),
        "historical_documents": ArchiveStore(db).stats(),
    }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
