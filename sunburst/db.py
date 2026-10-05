from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

SCHEMA = r"""
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS comment_pages (
    url TEXT PRIMARY KEY,
    file_number TEXT,
    title TEXT,
    release_numbers TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY,
    file_number TEXT,
    received_date TEXT,
    letter_type TEXT,
    commenter TEXT,
    source_url TEXT NOT NULL UNIQUE,
    media_type TEXT,
    body TEXT,
    content_sha256 TEXT,
    fetched_at TEXT NOT NULL,
    extraction_error TEXT
);

CREATE INDEX IF NOT EXISTS comments_file_number_idx ON comments(file_number);
CREATE INDEX IF NOT EXISTS comments_commenter_idx ON comments(commenter);
CREATE INDEX IF NOT EXISTS comments_received_date_idx ON comments(received_date);

CREATE VIRTUAL TABLE IF NOT EXISTS comments_fts USING fts5(
    commenter,
    file_number,
    letter_type,
    body,
    content='comments',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS comments_ai AFTER INSERT ON comments BEGIN
    INSERT INTO comments_fts(rowid, commenter, file_number, letter_type, body)
    VALUES (new.id, new.commenter, new.file_number, new.letter_type, new.body);
END;

CREATE TRIGGER IF NOT EXISTS comments_ad AFTER DELETE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, commenter, file_number, letter_type, body)
    VALUES ('delete', old.id, old.commenter, old.file_number, old.letter_type, old.body);
END;

CREATE TRIGGER IF NOT EXISTS comments_au AFTER UPDATE ON comments BEGIN
    INSERT INTO comments_fts(comments_fts, rowid, commenter, file_number, letter_type, body)
    VALUES ('delete', old.id, old.commenter, old.file_number, old.letter_type, old.body);
    INSERT INTO comments_fts(rowid, commenter, file_number, letter_type, body)
    VALUES (new.id, new.commenter, new.file_number, new.letter_type, new.body);
END;

CREATE TABLE IF NOT EXISTS crawl_queue (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('page', 'document')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'done', 'error')),
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_from TEXT,
    file_number TEXT,
    received_date TEXT,
    letter_type TEXT,
    commenter TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS crawl_queue_status_idx ON crawl_queue(status, kind);
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def enqueue(
        self,
        url: str,
        kind: str,
        *,
        discovered_from: str | None = None,
        file_number: str | None = None,
        received_date: str | None = None,
        letter_type: str | None = None,
        commenter: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO crawl_queue(
                    url, kind, discovered_from, file_number, received_date,
                    letter_type, commenter, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    file_number=COALESCE(crawl_queue.file_number, excluded.file_number),
                    received_date=COALESCE(crawl_queue.received_date, excluded.received_date),
                    letter_type=COALESCE(crawl_queue.letter_type, excluded.letter_type),
                    commenter=COALESCE(crawl_queue.commenter, excluded.commenter)
                """,
                (url, kind, discovered_from, file_number, received_date, letter_type, commenter, now),
            )

    def reset_processing(self) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crawl_queue SET status='pending', updated_at=? WHERE status='processing'",
                (utc_now(),),
            )

    def next_item(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM crawl_queue
                WHERE status='pending'
                ORDER BY CASE kind WHEN 'page' THEN 0 ELSE 1 END, rowid
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE crawl_queue
                SET status='processing', attempts=attempts+1, updated_at=?
                WHERE url=?
                """,
                (utc_now(), row["url"]),
            )
            return dict(row)

    def finish_item(self, url: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE crawl_queue SET status='done', last_error=NULL, updated_at=? WHERE url=?",
                (utc_now(), url),
            )

    def fail_item(self, url: str, error: str, *, retry: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE crawl_queue
                SET status=?, last_error=?, updated_at=?
                WHERE url=?
                """,
                ("pending" if retry else "error", error[:2000], utc_now(), url),
            )

    def upsert_page(
        self,
        *,
        url: str,
        file_number: str | None,
        title: str | None,
        release_numbers: str | None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO comment_pages(url, file_number, title, release_numbers, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    file_number=excluded.file_number,
                    title=excluded.title,
                    release_numbers=excluded.release_numbers,
                    fetched_at=excluded.fetched_at
                """,
                (url, file_number, title, release_numbers, utc_now()),
            )

    def upsert_comment(
        self,
        *,
        file_number: str | None,
        received_date: str | None,
        letter_type: str | None,
        commenter: str | None,
        source_url: str,
        media_type: str | None,
        body: str | None,
        content_sha256: str | None,
        extraction_error: str | None = None,
    ) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO comments(
                    file_number, received_date, letter_type, commenter, source_url,
                    media_type, body, content_sha256, fetched_at, extraction_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET
                    file_number=COALESCE(excluded.file_number, comments.file_number),
                    received_date=COALESCE(excluded.received_date, comments.received_date),
                    letter_type=COALESCE(excluded.letter_type, comments.letter_type),
                    commenter=COALESCE(excluded.commenter, comments.commenter),
                    media_type=excluded.media_type,
                    body=excluded.body,
                    content_sha256=excluded.content_sha256,
                    fetched_at=excluded.fetched_at,
                    extraction_error=excluded.extraction_error
                """,
                (
                    file_number,
                    received_date,
                    letter_type,
                    commenter,
                    source_url,
                    media_type,
                    body,
                    content_sha256,
                    utc_now(),
                    extraction_error,
                ),
            )
            row = conn.execute("SELECT id FROM comments WHERE source_url=?", (source_url,)).fetchone()
            assert row is not None
            return int(row["id"])

    def get_comment(self, comment_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
            return dict(row) if row else None

    def search_comments(
        self,
        query: str,
        *,
        file_number: str | None = None,
        commenter: str | None = None,
        letter_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        clauses = ["comments_fts MATCH ?"]
        params: list[Any] = [query]
        if file_number:
            clauses.append("c.file_number = ?")
            params.append(file_number)
        if commenter:
            clauses.append("c.commenter LIKE ?")
            params.append(f"%{commenter}%")
        if letter_type:
            clauses.append("c.letter_type = ?")
            params.append(letter_type)
        params.extend([limit, offset])
        sql = f"""
            SELECT
                c.id, c.file_number, c.received_date, c.letter_type, c.commenter,
                c.source_url, c.media_type,
                snippet(comments_fts, 3, '[', ']', ' … ', 28) AS snippet,
                bm25(comments_fts) AS score
            FROM comments_fts
            JOIN comments c ON c.id = comments_fts.rowid
            WHERE {' AND '.join(clauses)}
            ORDER BY score
            LIMIT ? OFFSET ?
        """
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def list_files(self, prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT file_number, COUNT(*) AS comment_count,
                           MIN(received_date) AS first_received,
                           MAX(received_date) AS last_received
                    FROM comments
                    WHERE file_number IS NOT NULL AND file_number LIKE ?
                    GROUP BY file_number
                    ORDER BY file_number
                    LIMIT ?
                    """,
                    (f"{prefix}%", limit),
                ).fetchall()
            ]

    def stats(self) -> dict[str, Any]:
        with self.connect() as conn:
            comments = conn.execute("SELECT COUNT(*) AS n FROM comments").fetchone()["n"]
            searchable = conn.execute(
                "SELECT COUNT(*) AS n FROM comments WHERE body IS NOT NULL AND body <> ''"
            ).fetchone()["n"]
            files = conn.execute(
                "SELECT COUNT(DISTINCT file_number) AS n FROM comments WHERE file_number IS NOT NULL"
            ).fetchone()["n"]
            queue = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM crawl_queue GROUP BY status"
                ).fetchall()
            }
            return {
                "comments": comments,
                "searchable_comments": searchable,
                "file_numbers": files,
                "queue": queue,
            }
