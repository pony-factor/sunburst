import io
import zipfile
from pathlib import Path

from sunburst.db import Database
from sunburst.sec import extract_document, parse_comment_listing


def test_upsert_and_search(tmp_path: Path) -> None:
    db = Database(tmp_path / "comments.db")
    comment_id = db.upsert_comment(
        file_number="S7-2026-30",
        received_date="Sept. 5, 2026",
        letter_type="Public Comment",
        commenter="Example Commenter",
        source_url="https://www.sec.gov/comments/S7-2026-30/example.pdf",
        media_type="application/pdf",
        body="Transfer agents should maintain accurate ownership records.",
        content_sha256="abc123",
    )

    rows = db.search_comments("ownership", file_number="S7-2026-30")
    assert len(rows) == 1
    assert rows[0]["id"] == comment_id
    assert rows[0]["commenter"] == "Example Commenter"

    full = db.get_comment(comment_id)
    assert full is not None
    assert "accurate ownership records" in full["body"]


def test_queue_is_resumable(tmp_path: Path) -> None:
    db = Database(tmp_path / "comments.db")
    url = "https://www.sec.gov/rules-regulations/public-comments/s7-2026-30"
    db.enqueue(url, "page")
    item = db.next_item()
    assert item is not None and item["url"] == url
    db.reset_processing()
    resumed = db.next_item()
    assert resumed is not None and resumed["url"] == url


def test_parse_modern_comment_page() -> None:
    html = b"""
    <html><body><main>
      <h1>Comments on Transfer Agent Rules</h1>
      <p>File Number S7-2026-30</p>
      <table><tbody>
        <tr><td>Sept. 5, 2026</td><td>Public Comment</td>
          <td><a href="/comments/S7-2026-30/s7202630-123-456.pdf">Pamela Norton</a></td></tr>
        <tr><td>Sept. 4, 2026</td><td>Meeting with SEC Officials</td>
          <td><a href="/comments/S7-2026-30/meeting.htm">Memorandum from Trading and Markets</a></td></tr>
      </tbody></table>
    </main></body></html>
    """
    meta, comments, _ = parse_comment_listing(
        html, "https://www.sec.gov/rules-regulations/public-comments/s7-2026-30"
    )
    assert meta["file_number"] == "S7-2026-30"
    assert len(comments) == 2
    assert comments[0].received_date == "Sept. 5, 2026"
    assert comments[0].commenter == "Pamela Norton"


def test_extract_html() -> None:
    body, error = extract_document(
        b"<html><body><main><h1>Letter</h1><p>Hello SEC.</p></main></body></html>",
        "text/html",
        "https://www.sec.gov/comments/s7-27-15/example.htm",
    )
    assert error is None
    assert body == "Letter\nHello SEC."


def test_extract_docx() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="urn:test"><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p><w:p><w:r><w:t>SEC</w:t></w:r></w:p></w:body></w:document>',
        )
    body, error = extract_document(
        buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "https://www.sec.gov/comments/s7-27-15/example.docx",
    )
    assert error is None
    assert body is not None
    assert "Hello" in body and "SEC" in body
