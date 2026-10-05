from __future__ import annotations

import urllib.error
from dataclasses import dataclass
from typing import Callable

from .db import Database
from .sec import (
    DEFAULT_SEEDS,
    SECClient,
    canonicalize_url,
    discover_links,
    extract_document,
    is_comment_listing_url,
    parse_comment_listing,
    sha256,
)


@dataclass(slots=True)
class IngestResult:
    processed: int = 0
    pages: int = 0
    documents: int = 0
    errors: int = 0


class Ingestor:
    def __init__(self, db: Database, client: SECClient, *, logger: Callable[[str], None] = print):
        self.db = db
        self.client = client
        self.log = logger

    def seed(self, urls: tuple[str, ...] | list[str] | None = None) -> None:
        for url in urls or DEFAULT_SEEDS:
            self.db.enqueue(canonicalize_url(url), "page")

    def run(self, *, max_items: int | None = None) -> IngestResult:
        self.db.reset_processing()
        result = IngestResult()
        while max_items is None or result.processed < max_items:
            item = self.db.next_item()
            if item is None:
                break
            url = item["url"]
            try:
                fetched = self.client.fetch(url)
                if item["kind"] == "page":
                    self._process_page(url, fetched.content)
                    result.pages += 1
                else:
                    self._process_document(item, fetched.content, fetched.content_type)
                    result.documents += 1
                self.db.finish_item(url)
            except Exception as exc:
                attempts = int(item.get("attempts", 0)) + 1
                retry = _retryable(exc) and attempts < 5
                self.db.fail_item(url, f"{type(exc).__name__}: {exc}", retry=retry)
                result.errors += 1
                self.log(f"error {url}: {exc}")
            result.processed += 1
        return result

    def _process_page(self, url: str, content: bytes) -> None:
        if is_comment_listing_url(url):
            metadata, comments, page_links = parse_comment_listing(content, url)
            self.db.upsert_page(url=url, **metadata)
            for comment in comments:
                self.db.enqueue(
                    comment.url,
                    "document",
                    discovered_from=url,
                    file_number=comment.file_number,
                    received_date=comment.received_date,
                    letter_type=comment.letter_type,
                    commenter=comment.commenter,
                )
            for link in page_links:
                self.db.enqueue(link, "page", discovered_from=url)
            self.log(f"comments page {url}: {len(comments)} documents")
            return

        links = discover_links(content, url)
        for link in links:
            self.db.enqueue(link, "page", discovered_from=url)
        self.log(f"discovery page {url}: {len(links)} links")

    def _process_document(self, item: dict, content: bytes, content_type: str) -> None:
        body, extraction_error = extract_document(content, content_type, item["url"])
        comment_id = self.db.upsert_comment(
            file_number=item.get("file_number"),
            received_date=item.get("received_date"),
            letter_type=item.get("letter_type"),
            commenter=item.get("commenter"),
            source_url=item["url"],
            media_type=content_type,
            body=body,
            content_sha256=sha256(content),
            extraction_error=extraction_error,
        )
        self.log(f"comment {comment_id}: {item['url']}")


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {429, 500, 502, 503, 504}
    return isinstance(exc, (urllib.error.URLError, TimeoutError))
