from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .db import Database, utc_now

ARCHIVE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS sec_documents (
    id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT,
    document_date TEXT,
    author TEXT,
    release_number TEXT,
    file_number TEXT,
    parent_url TEXT,
    source_url TEXT NOT NULL UNIQUE,
    media_type TEXT,
    body TEXT,
    content_sha256 TEXT,
    blob_path TEXT,
    fetched_at TEXT NOT NULL,
    extraction_error TEXT
);

CREATE INDEX IF NOT EXISTS sec_documents_category_idx ON sec_documents(category);
CREATE INDEX IF NOT EXISTS sec_documents_date_idx ON sec_documents(document_date);
CREATE INDEX IF NOT EXISTS sec_documents_release_idx ON sec_documents(release_number);
CREATE INDEX IF NOT EXISTS sec_documents_file_idx ON sec_documents(file_number);

CREATE VIRTUAL TABLE IF NOT EXISTS sec_documents_fts USING fts5(
    title,
    category,
    author,
    release_number,
    file_number,
    body,
    content='sec_documents',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS sec_documents_ai AFTER INSERT ON sec_documents BEGIN
    INSERT INTO sec_documents_fts(
        rowid, title, category, author, release_number, file_number, body
    ) VALUES (
        new.id, new.title, new.category, new.author, new.release_number, new.file_number, new.body
    );
END;

CREATE TRIGGER IF NOT EXISTS sec_documents_ad AFTER DELETE ON sec_documents BEGIN
    INSERT INTO sec_documents_fts(
        sec_documents_fts, rowid, title, category, author, release_number, file_number, body
    ) VALUES (
        'delete', old.id, old.title, old.category, old.author, old.release_number, old.file_number, old.body
    );
END;

CREATE TRIGGER IF NOT EXISTS sec_documents_au AFTER UPDATE ON sec_documents BEGIN
    INSERT INTO sec_documents_fts(
        sec_documents_fts, rowid, title, category, author, release_number, file_number, body
    ) VALUES (
        'delete', old.id, old.title, old.category, old.author, old.release_number, old.file_number, old.body
    );
    INSERT INTO sec_documents_fts(
        rowid, title, category, author, release_number, file_number, body
    ) VALUES (
        new.id, new.title, new.category, new.author, new.release_number, new.file_number, new.body
    );
END;

CREATE TABLE IF NOT EXISTS archive_queue (
    url TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('page', 'document')),
    category TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'processing', 'done', 'error')),
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_from TEXT,
    title TEXT,
    document_date TEXT,
    author TEXT,
    release_number TEXT,
    file_number TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS archive_queue_status_idx
    ON archive_queue(status, kind, category);
"""


class ArchiveStore:
    """Historical SEC document storage sharing Sunburst's SQLite database."""

    def __init__(self, db: Database):
        self.db = db
        with self.db.connect() as conn:
            conn.executescript(ARCHIVE_SCHEMA)

    def enqueue(
        self,
        url: str,
        kind: str,
        *,
        category: str,
        discovered_from: str | None = None,
        title: str | None = None,
        document_date: str | None = None,
        author: str | None = None,
        release_number: str | None = None,
        file_number: str | None = None,
    ) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO archive_queue(
                    url, kind, category, discovered_from, title, document_date,
                    author, release_number, file_number, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    category=COALESCE(archive_queue.category, excluded.category),
                    title=COALESCE(archive_queue.title, excluded.title),
                    document_date=COALESCE(archive_queue.document_date, excluded.document_date),
                    author=COALESCE(archive_queue.author, excluded.author),
                    release_number=COALESCE(archive_queue.release_number, excluded.release_number),
                    file_number=COALESCE(archive_queue.file_number, excluded.file_number)
                """,
                (
                    url,
                    kind,
                    category,
                    discovered_from,
                    title,
                    document_date,
                    author,
                    release_number,
                    file_number,
                    utc_now(),
                ),
            )

    def reset_processing(self) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE archive_queue SET status='pending', updated_at=? WHERE status='processing'",
                (utc_now(),),
            )

    def next_item(self) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM archive_queue
                WHERE status='pending'
                ORDER BY CASE kind WHEN 'page' THEN 0 ELSE 1 END, rowid
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE archive_queue
                SET status='processing', attempts=attempts+1, updated_at=?
                WHERE url=?
                """,
                (utc_now(), row["url"]),
            )
            return dict(row)

    def finish_item(self, url: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE archive_queue
                SET status='done', last_error=NULL, updated_at=?
                WHERE url=?
                """,
                (utc_now(), url),
            )

    def fail_item(self, url: str, error: str, *, retry: bool) -> None:
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE archive_queue
                SET status=?, last_error=?, updated_at=?
                WHERE url=?
                """,
                ("pending" if retry else "error", error[:2000], utc_now(), url),
            )

    def upsert_document(
        self,
        *,
        category: str,
        title: str | None,
        document_date: str | None,
        author: str | None,
        release_number: str | None,
        file_number: str | None,
        parent_url: str | None,
        source_url: str,
        media_type: str | None,
        body: str | None,
        content_sha256: str | None,
        blob_path: str | None,
        extraction_error: str | None = None,
    ) -> int:
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO sec_documents(
                    category, title, document_date, author, release_number, file_number,
                    parent_url, source_url, media_type, body, content_sha256, blob_path,
                    fetched_at, extraction_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_url) DO UPDATE SET
                    category=excluded.category,
                    title=COALESCE(excluded.title, sec_documents.title),
                    document_date=COALESCE(excluded.document_date, sec_documents.document_date),
                    author=COALESCE(excluded.author, sec_documents.author),
                    release_number=COALESCE(excluded.release_number, sec_documents.release_number),
                    file_number=COALESCE(excluded.file_number, sec_documents.file_number),
                    parent_url=COALESCE(excluded.parent_url, sec_documents.parent_url),
                    media_type=excluded.media_type,
                    body=excluded.body,
                    content_sha256=excluded.content_sha256,
                    blob_path=COALESCE(excluded.blob_path, sec_documents.blob_path),
                    fetched_at=excluded.fetched_at,
                    extraction_error=excluded.extraction_error
                """,
                (
                    category,
                    title,
                    document_date,
                    author,
                    release_number,
                    file_number,
                    parent_url,
                    source_url,
                    media_type,
                    body,
                    content_sha256,
                    blob_path,
                    utc_now(),
                    extraction_error,
                ),
            )
            row = conn.execute(
                "SELECT id FROM sec_documents WHERE source_url=?",
                (source_url,),
            ).fetchone()
            assert row is not None
            return int(row["id"])

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sec_documents WHERE id=?",
                (document_id,),
            ).fetchone()
            return dict(row) if row else None

    def search_documents(
        self,
        query: str,
        *,
        category: str | None = None,
        year: int | None = None,
        release_number: str | None = None,
        file_number: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        clauses = ["sec_documents_fts MATCH ?"]
        params: list[Any] = [query]
        if category:
            clauses.append("d.category = ?")
            params.append(category)
        if year:
            clauses.append("d.document_date LIKE ?")
            params.append(f"{year:04d}-%")
        if release_number:
            clauses.append("d.release_number LIKE ?")
            params.append(f"%{release_number}%")
        if file_number:
            clauses.append("d.file_number LIKE ?")
            params.append(f"%{file_number}%")
        params.extend([limit, offset])
        sql = f"""
            SELECT
                d.id, d.category, d.title, d.document_date, d.author,
                d.release_number, d.file_number, d.source_url, d.media_type,
                snippet(sec_documents_fts, 5, '[', ']', ' … ', 32) AS snippet,
                bm25(sec_documents_fts) AS score
            FROM sec_documents_fts
            JOIN sec_documents d ON d.id = sec_documents_fts.rowid
            WHERE {' AND '.join(clauses)}
            ORDER BY score
            LIMIT ? OFFSET ?
        """
        with self.db.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def list_categories(self) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT category, COUNT(*) AS document_count,
                           MIN(document_date) AS first_date,
                           MAX(document_date) AS last_date
                    FROM sec_documents
                    GROUP BY category
                    ORDER BY category
                    """
                ).fetchall()
            ]

    def stats(self) -> dict[str, Any]:
        with self.db.connect() as conn:
            documents = conn.execute(
                "SELECT COUNT(*) AS n FROM sec_documents"
            ).fetchone()["n"]
            searchable = conn.execute(
                """
                SELECT COUNT(*) AS n FROM sec_documents
                WHERE body IS NOT NULL AND body <> ''
                """
            ).fetchone()["n"]
            blobs = conn.execute(
                """
                SELECT COUNT(*) AS n FROM sec_documents
                WHERE blob_path IS NOT NULL AND blob_path <> ''
                """
            ).fetchone()["n"]
            queue = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM archive_queue GROUP BY status"
                ).fetchall()
            }
            return {
                "documents": documents,
                "searchable_documents": searchable,
                "stored_blobs": blobs,
                "categories": self.list_categories(),
                "queue": queue,
            }
