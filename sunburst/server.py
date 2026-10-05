from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from .db import Database

mcp = MCPServer("sunburst")


def _database() -> Database:
    return Database(Path(os.getenv("SUNBURST_DB", "sunburst.db")))


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
def index_stats() -> dict[str, Any]:
    """Return corpus size and crawl queue status."""
    return _database().stats()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
