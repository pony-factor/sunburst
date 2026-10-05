from __future__ import annotations

import hashlib
import io
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from email.message import Message
from html import unescape
from pathlib import PurePosixPath
from typing import Iterable
from xml.etree import ElementTree

from bs4 import BeautifulSoup
from pypdf import PdfReader

SEC_ORIGIN = "https://www.sec.gov"
DEFAULT_SEEDS = (
    f"{SEC_ORIGIN}/rules-regulations/rulemaking-activity",
    f"{SEC_ORIGIN}/rules-regulations/self-regulatory-organization-rulemaking",
    f"{SEC_ORIGIN}/rules-regulations/public-company-accounting-oversight-board-rulemaking",
    f"{SEC_ORIGIN}/rules-regulations/petitions-rulemaking-submitted-to-sec",
    f"{SEC_ORIGIN}/rules-regulations/submit-public-comments",
    f"{SEC_ORIGIN}/files/rules.shtml",
)

FILE_NUMBER_RE = re.compile(
    r"\b(?:"
    r"S7-\d{1,4}-\d{1,4}"
    r"|SR-[A-Za-z0-9.-]+-\d{4}-\d{1,4}"
    r"|4-\d{2,4}"
    r"|265-\d{1,4}"
    r"|PCAOB-\d{4}-\d{1,4}"
    r")\b",
    re.IGNORECASE,
)
RELEASE_RE = re.compile(r"\b(?:33|34|39|IA|IC)-\d{3,6}\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:Jan\.?|Feb\.?|Mar\.?|Apr\.?|May|June?|July?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"
    r"\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)

DOCUMENT_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".htm",
    ".html",
    ".doc",
    ".docx",
}


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    content_type: str


@dataclass(slots=True)
class CommentLink:
    url: str
    file_number: str | None
    received_date: str | None
    letter_type: str | None
    commenter: str | None


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    absolute = urllib.parse.urljoin(base_url or SEC_ORIGIN, url)
    parts = urllib.parse.urlsplit(absolute)
    host = parts.hostname.lower() if parts.hostname else ""
    if host not in {"sec.gov", "www.sec.gov"}:
        raise ValueError(f"Refusing non-SEC URL: {absolute}")
    scheme = "https"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = urllib.parse.urlencode(sorted(query_pairs))
    return urllib.parse.urlunsplit((scheme, "www.sec.gov", path, query, ""))


def file_number_from_text(*values: str | None) -> str | None:
    for value in values:
        if not value:
            continue
        match = FILE_NUMBER_RE.search(value)
        if match:
            return match.group(0).upper()
    return None


def is_document_url(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    suffix = PurePosixPath(path).suffix
    if suffix in DOCUMENT_EXTENSIONS:
        return True
    return "/comments/" in path and not path.endswith("/") and "type" not in PurePosixPath(path).stem


def is_comment_listing_url(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    if path.startswith("/rules-regulations/public-comments/"):
        return True
    if path.startswith("/comments/") and path.endswith((".shtml", ".htm", ".html")):
        name = PurePosixPath(path).name
        return bool(re.fullmatch(r"[a-z0-9-]+\.(?:shtml|html?|htm)", name)) and "-type" not in name
    return False


def is_discovery_page(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.lower()
    if is_comment_listing_url(url):
        return True
    allowed_prefixes = (
        "/rules-regulations/rulemaking-activity",
        "/rules-regulations/self-regulatory-organization-rulemaking",
        "/rules-regulations/public-company-accounting-oversight-board-rulemaking",
        "/rules-regulations/petitions-rulemaking-submitted-to-sec",
        "/rules-regulations/submit-public-comments",
        "/rules-regulations/20",
        "/rules/proposed",
        "/rules/concept",
        "/rules/final",
        "/rules/interim-final",
        "/rules/interpretive",
        "/rules/policy",
        "/rules/pcaob",
        "/rules/sro",
        "/rules/petitions",
        "/rules/other",
        "/files/rules.shtml",
    )
    return any(path.startswith(prefix) for prefix in allowed_prefixes)


class SECClient:
    def __init__(
        self,
        user_agent: str | None = None,
        *,
        requests_per_second: float = 4.0,
        timeout: float = 45.0,
        retries: int = 5,
    ):
        self.user_agent = (user_agent or os.getenv("SUNBURST_USER_AGENT", "")).strip()
        if not self.user_agent:
            raise ValueError(
                "Set SUNBURST_USER_AGENT to a declared identity such as "
                "'Example Researcher research@example.com'."
            )
        if requests_per_second <= 0 or requests_per_second > 9.0:
            raise ValueError("requests_per_second must be > 0 and <= 9")
        self.interval = 1.0 / requests_per_second
        self.timeout = timeout
        self.retries = retries
        self._last_request = 0.0

    def _wait(self) -> None:
        remaining = self.interval - (time.monotonic() - self._last_request)
        if remaining > 0:
            time.sleep(remaining)

    def fetch(self, url: str) -> FetchResult:
        url = canonicalize_url(url)
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "identity",
                    "Accept": "text/html,application/pdf,text/plain,application/vnd.openxmlformats-officedocument.wordprocessingml.document,*/*;q=0.5",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._last_request = time.monotonic()
                    content_type = _content_type(response.headers)
                    return FetchResult(
                        url=response.geturl(),
                        status=response.status,
                        content=response.read(),
                        content_type=content_type,
                    )
            except urllib.error.HTTPError as exc:
                self._last_request = time.monotonic()
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(60.0, 2**attempt)
                time.sleep(delay)
            except urllib.error.URLError as exc:
                self._last_request = time.monotonic()
                last_error = exc
                if attempt >= self.retries:
                    raise
                time.sleep(min(60.0, 2**attempt))
        assert last_error is not None
        raise last_error


def _content_type(headers: Message) -> str:
    value = headers.get_content_type()
    return value.lower() if value else "application/octet-stream"


def parse_comment_listing(html: bytes, page_url: str) -> tuple[dict[str, str | None], list[CommentLink], list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title_node = soup.find("h1") or soup.find("title")
    title = " ".join(title_node.stripped_strings) if title_node else None
    text = " ".join(soup.stripped_strings)
    file_number = file_number_from_text(page_url, text)
    releases = sorted(set(match.group(0).upper() for match in RELEASE_RE.finditer(text)))

    comment_links: dict[str, CommentLink] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        anchor = row.find("a", href=True)
        if not anchor:
            continue
        try:
            url = canonicalize_url(anchor["href"], page_url)
        except ValueError:
            continue
        if not _looks_like_comment_document(url, page_url):
            continue
        cell_texts = [" ".join(cell.stripped_strings) for cell in cells]
        row_text = " | ".join(cell_texts)
        received = _extract_date(cell_texts[0] if cell_texts else row_text)
        letter_type = _infer_letter_type(cell_texts)
        commenter = " ".join(anchor.stripped_strings).strip() or None
        comment_links[url] = CommentLink(
            url=url,
            file_number=file_number,
            received_date=received,
            letter_type=letter_type,
            commenter=commenter,
        )

    # Legacy SEC comment pages and some Drupal views are not always table-shaped.
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonicalize_url(anchor["href"], page_url)
        except ValueError:
            continue
        if not _looks_like_comment_document(url, page_url):
            continue
        if url in comment_links:
            continue
        commenter = " ".join(anchor.stripped_strings).strip() or None
        parent_text = " ".join(anchor.parent.stripped_strings) if anchor.parent else commenter or ""
        comment_links[url] = CommentLink(
            url=url,
            file_number=file_number,
            received_date=_extract_date(parent_text),
            letter_type="Meeting with SEC Officials" if "meeting" in parent_text.lower() else "Public Comment",
            commenter=commenter,
        )

    page_links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        try:
            url = canonicalize_url(href, page_url)
        except ValueError:
            continue
        if is_discovery_page(url):
            page_links.add(url)

    return (
        {
            "file_number": file_number,
            "title": title,
            "release_numbers": ",".join(releases) if releases else None,
        },
        list(comment_links.values()),
        sorted(page_links),
    )


def discover_links(html: bytes, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        try:
            url = canonicalize_url(anchor["href"], page_url)
        except ValueError:
            continue
        if is_discovery_page(url):
            links.add(url)
    return sorted(links)


def _looks_like_comment_document(url: str, listing_url: str) -> bool:
    path = urllib.parse.urlsplit(url).path.lower()
    listing_path = urllib.parse.urlsplit(listing_url).path.lower()
    if "/comments/" not in path:
        return False
    if url == listing_url:
        return False
    if "submit" in path or path.endswith("/how-submit-comment"):
        return False
    if "-type" in PurePosixPath(path).stem:
        return True
    suffix = PurePosixPath(path).suffix
    if suffix in DOCUMENT_EXTENSIONS:
        # Do not treat another legacy listing page as a comment document.
        if path.endswith(".shtml") and PurePosixPath(path).name == PurePosixPath(listing_path).name:
            return False
        return True
    return False


def _extract_date(value: str) -> str | None:
    match = DATE_RE.search(value)
    return unescape(match.group(0)).strip() if match else None


def _infer_letter_type(cells: Iterable[str]) -> str | None:
    normalized = [cell.strip() for cell in cells if cell.strip()]
    for cell in normalized:
        low = cell.lower()
        if "meeting with sec" in low:
            return "Meeting with SEC Officials"
        if "public comment" in low:
            return "Public Comment"
    if len(normalized) >= 2 and normalized[1] and not DATE_RE.search(normalized[1]):
        return normalized[1]
    return "Public Comment"


def extract_document(content: bytes, content_type: str, url: str) -> tuple[str | None, str | None]:
    path = urllib.parse.urlsplit(url).path.lower()
    suffix = PurePosixPath(path).suffix
    try:
        if content_type == "application/pdf" or suffix == ".pdf":
            reader = PdfReader(io.BytesIO(content))
            body = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()
            return body or None, None if body else "PDF contained no extractable text"
        if content_type in {"text/html", "application/xhtml+xml"} or suffix in {".htm", ".html", ".shtml"}:
            soup = BeautifulSoup(content, "html.parser")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            main = soup.find("main") or soup.find(id="main-content") or soup.body or soup
            body = "\n".join(line.strip() for line in main.stripped_strings if line.strip())
            return body or None, None if body else "HTML contained no extractable text"
        if content_type in {"application/xml", "text/xml"} or suffix == ".xml":
            root = ElementTree.fromstring(content)
            body = "\n".join(
                text.strip()
                for text in root.itertext()
                if text and text.strip()
            )
            return body or None, None if body else "XML contained no extractable text"
        if content_type.startswith("text/") or suffix == ".txt":
            return _decode_text(content).strip() or None, None
        if suffix == ".docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return _extract_docx(content), None
        if suffix == ".doc" or content_type in {"application/msword", "application/vnd.ms-word"}:
            return None, "Legacy binary .doc extraction is not supported yet"
        if suffix in {".xls", ".xlsx", ".ppt", ".pptx", ".zip"}:
            return None, f"Binary {suffix} extraction is not supported yet"
        return _decode_text(content).strip() or None, None
    except Exception as exc:  # Keep metadata even when one document is malformed.
        return None, f"{type(exc).__name__}: {exc}"


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "windows-1252", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _extract_docx(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    texts: list[str] = []
    for element in root.iter():
        if element.tag.endswith("}t") and element.text:
            texts.append(element.text)
        elif element.tag.endswith("}p") and texts and texts[-1] != "\n":
            texts.append("\n")
    return "".join(texts).strip()


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
