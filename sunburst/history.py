from __future__ import annotations

import os
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from bs4 import BeautifulSoup

from .archive import ArchiveStore
from .db import Database
from .sec import SECClient, canonicalize_url, extract_document, file_number_from_text, sha256

HISTORICAL_SEEDS: tuple[tuple[str, str, str], ...] = (
    ("reports", "https://www.sec.gov/reports?order=field_publish_date&sort=asc", "page"),
    (
        "speeches-statements",
        "https://www.sec.gov/newsroom/speeches-statements/speeches-statements-archive?order=field_publish_date&sort=asc",
        "page",
    ),
    (
        "press-releases",
        "https://www.sec.gov/newsroom/press-releases?order=field_publish_date&sort=asc",
        "page",
    ),
    (
        "litigation-releases",
        "https://www.sec.gov/enforcement-litigation/litigation-releases?order=field_publish_date&sort=asc",
        "page",
    ),
    (
        "administrative-proceedings",
        "https://www.sec.gov/enforcement-litigation/administrative-proceedings?order=field_publish_date&sort=asc",
        "page",
    ),
    (
        "administrative-case-materials",
        "https://www.sec.gov/litigation/apdocuments",
        "page",
    ),
    (
        "accounting-auditing-enforcement",
        "https://www.sec.gov/enforcement-litigation/accounting-auditing-enforcement-releases?order=field_publish_date&sort=asc",
        "page",
    ),
    (
        "policy-statements",
        "https://www.sec.gov/rules-regulations/policy-statements",
        "page",
    ),
    (
        "staff-guidance",
        "https://www.sec.gov/rules-regulations/staff-guidance",
        "page",
    ),
    (
        "staff-legal-bulletins",
        "https://www.sec.gov/rules-regulations/staff-guidance/staff-legal-bulletins",
        "page",
    ),
    (
        "staff-accounting-bulletins",
        "https://www.sec.gov/rules-regulations/staff-guidance/staff-accounting-bulletins",
        "page",
    ),
    (
        "no-action-letters",
        "https://www.sec.gov/divisions/corpfin/cfnew/cf-noaction.shtml",
        "page",
    ),
    ("rules", "https://www.sec.gov/files/rules.shtml", "page"),
    ("manuals", "https://www.sec.gov/file/enforcementmanual", "document"),
)

HISTORY_PREFIXES = (
    "/reports",
    "/newsroom/speeches-statements",
    "/newsroom/press-releases",
    "/news/",
    "/enforcement-litigation/",
    "/litigation/",
    "/rules-regulations/",
    "/rules/",
    "/divisions/enforce/friactions",
    "/divisions/corpfin/cfnew",
    "/divisions/corpfin/noaction",
    "/files/divisions/corpfin",
    "/files/rules.shtml",
)

BLOCKED_PREFIXES = (
    "/archives/edgar",
    "/ixviewer",
    "/cgi-bin/browse-edgar",
    "/search/",
)

BINARY_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".doc",
    ".docx",
    ".rtf",
    ".csv",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
}

RELEASE_RE = re.compile(
    r"\b(?:"
    r"LR-\d+"
    r"|AAER-\d+"
    r"|(?:33|34|35|39|IA|IC)-\d{2,6}"
    r"|SAB\s*\d+[A-Z]?"
    r"|SLB\s*\d+[A-Z]?"
    r")\b",
    re.IGNORECASE,
)

DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?\.?|Feb(?:ruary)?\.?|Mar(?:ch)?\.?|Apr(?:il)?\.?|May|"
    r"Jun(?:e)?\.?|Jul(?:y)?\.?|Aug(?:ust)?\.?|Sep(?:t(?:ember)?)?\.?|"
    r"Oct(?:ober)?\.?|Nov(?:ember)?\.?|Dec(?:ember)?\.?)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class HistoricalLink:
    url: str
    kind: str
    category: str
    title: str | None = None
    document_date: str | None = None
    author: str | None = None
    release_number: str | None = None
    file_number: str | None = None


@dataclass(slots=True)
class HistoricalIngestResult:
    processed: int = 0
    pages: int = 0
    documents: int = 0
    errors: int = 0


def normalize_history_url(url: str, base_url: str | None = None) -> str:
    canonical = canonicalize_url(url, base_url)
    parts = urllib.parse.urlsplit(canonical)
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() in {"page", "order", "sort"}
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(sorted(kept)), "")
    )


def is_historical_target(url: str, *, parent_url: str | None = None) -> bool:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.lower()
    if any(path.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        return False
    if any(path.startswith(prefix) for prefix in HISTORY_PREFIXES):
        return True

    suffix = PurePosixPath(path).suffix
    if parent_url and suffix in BINARY_EXTENSIONS:
        return True
    if parent_url and (path.startswith("/file/") or path.startswith("/files/")):
        return True
    return False


def category_for_url(url: str, fallback: str = "historical") -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    if "/speeches-statements" in path or path.startswith("/news/speech"):
        return "speeches-statements"
    if "/press-releases" in path or path.startswith("/news/press"):
        return "press-releases"
    if "accounting-auditing-enforcement" in path or "/friactions" in path:
        return "accounting-auditing-enforcement"
    if "litreleases" in path or "litigation-releases" in path:
        return "litigation-releases"
    if "administrative-proceedings" in path or "/litigation/admin" in path or "apdocuments" in path:
        return "administrative-proceedings"
    if "/litigation/" in path:
        return "enforcement-litigation"
    if path.startswith("/reports"):
        return "reports"
    if "policy-statements" in path:
        return "policy-statements"
    if "staff-legal-bulletins" in path:
        return "staff-legal-bulletins"
    if "staff-accounting-bulletins" in path:
        return "staff-accounting-bulletins"
    if "staff-guidance" in path:
        return "staff-guidance"
    if "noaction" in path or "no-action" in path:
        return "no-action-letters"
    if path.startswith("/rules/") or path.startswith("/rules-regulations/"):
        return "rules"
    if path.startswith("/file/") and "manual" in path:
        return "manuals"
    return fallback


def parse_historical_page(
    html: bytes,
    page_url: str,
    fallback_category: str,
) -> list[HistoricalLink]:
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, HistoricalLink] = {}

    for anchor in soup.find_all("a", href=True):
        try:
            url = normalize_history_url(anchor["href"], page_url)
        except ValueError:
            continue
        if url == normalize_history_url(page_url):
            continue
        if not is_historical_target(url, parent_url=page_url):
            continue

        context_node = anchor.find_parent("tr") or anchor.parent
        context = " ".join(context_node.stripped_strings) if context_node else ""
        anchor_text = " ".join(anchor.stripped_strings).strip()
        title = _clean_title(anchor_text, context)
        category = category_for_url(url, fallback_category)
        release_number = _first_match(RELEASE_RE, f"{anchor_text} {context} {url}")
        file_number = file_number_from_text(anchor_text, context, url)
        document_date = _normalize_date(_first_match(DATE_RE, context))
        author = _infer_author(category, context_node)
        kind = _link_kind(url, page_url)

        found[url] = HistoricalLink(
            url=url,
            kind=kind,
            category=category,
            title=title,
            document_date=document_date,
            author=author,
            release_number=release_number.upper() if release_number else None,
            file_number=file_number,
        )

    return list(found.values())


def _link_kind(url: str, page_url: str) -> str:
    path = urllib.parse.urlsplit(url).path.lower()
    suffix = PurePosixPath(path).suffix
    if suffix in BINARY_EXTENSIONS:
        return "document"
    if path.startswith("/file/"):
        return "document"

    page_parts = urllib.parse.urlsplit(page_url)
    target_parts = urllib.parse.urlsplit(url)
    if target_parts.path == page_parts.path and target_parts.query != page_parts.query:
        return "page"

    leaf = PurePosixPath(path).name
    listing_names = {
        "litarchives.shtml",
        "apdocuments",
        "rules.shtml",
        "cf-noaction.shtml",
    }
    if leaf in listing_names or "archive" in leaf:
        return "page"
    return "document"


def _clean_title(anchor_text: str, context: str) -> str | None:
    generic = {"", "pdf", "html", "text", "document", "download", "here", "view"}
    if anchor_text.strip().lower() not in generic:
        return anchor_text.strip()[:1000]
    clean_context = " ".join(context.split())
    return clean_context[:1000] or None


def _infer_author(category: str, context_node) -> str | None:
    if category != "speeches-statements" or context_node is None:
        return None
    cells = context_node.find_all(["td", "th"])
    values = [" ".join(cell.stripped_strings).strip() for cell in cells]
    if len(values) >= 3:
        return values[2] or None
    return None


def _first_match(pattern: re.Pattern[str], value: str) -> str | None:
    match = pattern.search(value)
    return match.group(0) if match else None


def _normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"\.", "", value)
    cleaned = re.sub(r"\bSept\b", "Sep", cleaned, flags=re.IGNORECASE)
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class HistoricalIngestor:
    def __init__(
        self,
        db: Database,
        client: SECClient,
        *,
        blob_dir: str | Path | None = None,
        logger: Callable[[str], None] = print,
    ):
        self.db = db
        self.store = ArchiveStore(db)
        self.client = client
        self.log = logger
        configured = blob_dir or os.getenv("SUNBURST_BLOB_DIR")
        if configured:
            self.blob_dir = Path(configured)
        else:
            self.blob_dir = Path(db.path).resolve().parent / "blobs"

    def seed(
        self,
        seeds: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]] | None = None,
    ) -> None:
        for category, url, kind in seeds or HISTORICAL_SEEDS:
            self.store.enqueue(
                normalize_history_url(url),
                kind,
                category=category,
                title=None,
            )

    def run(self, *, max_items: int | None = None) -> HistoricalIngestResult:
        self.store.reset_processing()
        result = HistoricalIngestResult()
        while max_items is None or result.processed < max_items:
            item = self.store.next_item()
            if item is None:
                break
            try:
                fetched = self.client.fetch(item["url"])
                is_html = fetched.content_type in {"text/html", "application/xhtml+xml"} or (
                    PurePosixPath(urllib.parse.urlsplit(item["url"]).path).suffix.lower()
                    in {".htm", ".html", ".shtml"}
                )

                if item["kind"] == "document":
                    self._store_document(item, fetched.content, fetched.content_type)
                    result.documents += 1
                else:
                    result.pages += 1

                if is_html:
                    for link in parse_historical_page(
                        fetched.content,
                        item["url"],
                        item["category"],
                    ):
                        self.store.enqueue(
                            link.url,
                            link.kind,
                            category=link.category,
                            discovered_from=item["url"],
                            title=link.title,
                            document_date=link.document_date,
                            author=link.author,
                            release_number=link.release_number,
                            file_number=link.file_number,
                        )

                self.store.finish_item(item["url"])
            except Exception as exc:
                attempts = int(item.get("attempts", 0)) + 1
                retry = _retryable(exc) and attempts < 5
                self.store.fail_item(
                    item["url"],
                    f"{type(exc).__name__}: {exc}",
                    retry=retry,
                )
                result.errors += 1
                self.log(f"archive error {item['url']}: {exc}")
            result.processed += 1
        return result

    def _store_document(self, item: dict, content: bytes, media_type: str) -> None:
        digest = sha256(content)
        blob_path = self._persist_blob(content, digest, item["url"])
        body, extraction_error = extract_document(content, media_type, item["url"])
        document_id = self.store.upsert_document(
            category=item["category"],
            title=item.get("title"),
            document_date=item.get("document_date"),
            author=item.get("author"),
            release_number=item.get("release_number"),
            file_number=item.get("file_number"),
            parent_url=item.get("discovered_from"),
            source_url=item["url"],
            media_type=media_type,
            body=body,
            content_sha256=digest,
            blob_path=str(blob_path),
            extraction_error=extraction_error,
        )
        self.log(f"historical document {document_id}: {item['url']}")

    def _persist_blob(self, content: bytes, digest: str, url: str) -> Path:
        suffix = PurePosixPath(urllib.parse.urlsplit(url).path).suffix.lower()
        if not suffix or len(suffix) > 10:
            suffix = ".bin"
        target_dir = self.blob_dir / digest[:2]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{digest}{suffix}"
        if not target.exists():
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(content)
            temp.replace(target)
        return target


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {429, 500, 502, 503, 504}
    return isinstance(exc, (urllib.error.URLError, TimeoutError))
