from __future__ import annotations

from pathlib import Path

from sunburst.archive import ArchiveStore
from sunburst.db import Database
from sunburst.history import HistoricalIngestor, parse_historical_page
from sunburst.sec import FetchResult, extract_document


def test_archive_store_searches_historical_documents(tmp_path: Path) -> None:
    db = Database(tmp_path / "sunburst.db")
    archive = ArchiveStore(db)
    document_id = archive.upsert_document(
        category="reports",
        title="1935 Annual Report",
        document_date="1935-01-01",
        author="Securities and Exchange Commission",
        release_number=None,
        file_number=None,
        parent_url="https://www.sec.gov/reports",
        source_url="https://www.sec.gov/files/annual-report-1935.htm",
        media_type="text/html",
        body="The Commission reports on the first year of securities regulation.",
        content_sha256="abc123",
        blob_path="/tmp/abc123.htm",
    )

    rows = archive.search_documents("securities regulation", category="reports", year=1935)
    assert len(rows) == 1
    assert rows[0]["id"] == document_id
    assert rows[0]["title"] == "1935 Annual Report"

    full = archive.get_document(document_id)
    assert full is not None
    assert full["blob_path"] == "/tmp/abc123.htm"

    categories = archive.list_categories()
    assert categories == [
        {
            "category": "reports",
            "document_count": 1,
            "first_date": "1935-01-01",
            "last_date": "1935-01-01",
        }
    ]


def test_archive_queue_refines_rediscovered_document(tmp_path: Path) -> None:
    db = Database(tmp_path / "sunburst.db")
    archive = ArchiveStore(db)
    url = "https://www.sec.gov/files/example.pdf"

    archive.enqueue(url, "page", category="historical")
    archive.enqueue(
        url,
        "document",
        category="reports",
        discovered_from="https://www.sec.gov/reports",
        title="Example Report",
    )

    item = archive.next_item()
    assert item is not None
    assert item["kind"] == "document"
    assert item["category"] == "reports"
    assert item["discovered_from"] == "https://www.sec.gov/reports"
    assert item["title"] == "Example Report"


def test_parse_historical_page_recovers_metadata_and_pagination() -> None:
    html = b"""
    <html><body><main>
      <table>
        <tr>
          <td>Jan. 1, 1935</td>
          <td><a href="/files/annual-report-1935.pdf">1935 Annual Report (PDF)</a></td>
        </tr>
      </table>
      <a href="/reports?page=1&order=field_publish_date&sort=asc">Next</a>
    </main></body></html>
    """

    links = parse_historical_page(
        html,
        "https://www.sec.gov/reports?order=field_publish_date&sort=asc",
        "reports",
    )

    by_url = {link.url: link for link in links}
    report_url = "https://www.sec.gov/files/annual-report-1935.pdf"
    assert by_url[report_url].kind == "document"
    assert by_url[report_url].category == "reports"
    assert by_url[report_url].document_date == "1935-01-01"
    assert by_url[report_url].title == "1935 Annual Report (PDF)"

    page_url = "https://www.sec.gov/reports?order=field_publish_date&page=1&sort=asc"
    assert by_url[page_url].kind == "page"


def test_parse_adjudicatory_file_and_release_numbers() -> None:
    html = b"""
    <html><body><table>
      <tr>
        <td>Feb. 11, 2026</td>
        <td>
          <a href="/enforcement-litigation/administrative-law-judges-decisions/example">
            Epic Capital Wealth Advisors, LLC
          </a>
          Release No. 1418 File Number: 3-22307
        </td>
      </tr>
    </table></body></html>
    """

    links = parse_historical_page(
        html,
        "https://www.sec.gov/enforcement-litigation/administrative-law-judges-decisions",
        "alj-initial-decisions",
    )
    assert len(links) == 1
    assert links[0].release_number == "1418"
    assert links[0].file_number == "3-22307"
    assert links[0].document_date == "2026-02-11"


def test_sec_docket_xml_is_extractable() -> None:
    body, error = extract_document(
        b"<docket><release><title>Exchange Act Release</title><text>Market structure history.</text></release></docket>",
        "application/xml",
        "https://www.sec.gov/about/docket/2013/sec-docket-106-13.xml",
    )
    assert error is None
    assert body == "Exchange Act Release\nMarket structure history."


def test_unsupported_binary_is_preserved_without_gibberish_text() -> None:
    body, error = extract_document(
        b"PK\\x03\\x04not-really-an-xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "https://www.sec.gov/files/historical-workbook.xlsx",
    )
    assert body is None
    assert error == "Binary .xlsx extraction is not supported yet"


class FakeClient:
    def fetch(self, url: str) -> FetchResult:
        if url == "https://www.sec.gov/reports":
            return FetchResult(
                url=url,
                status=200,
                content=b"""
                    <html><body>
                      <a href="/news/speech/1934/example.htm">Historic Address</a>
                    </body></html>
                """,
                content_type="text/html",
            )
        if url == "https://www.sec.gov/news/speech/1934/example.htm":
            return FetchResult(
                url=url,
                status=200,
                content=b"""
                    <html><body><main>
                      <h1>Historic Address</h1>
                      <p>Federal securities regulation and investor protection.</p>
                    </main></body></html>
                """,
                content_type="text/html",
            )
        raise AssertionError(f"unexpected URL {url}")


def test_historical_ingestor_preserves_raw_blob_and_indexes_text(tmp_path: Path) -> None:
    db = Database(tmp_path / "sunburst.db")
    ingestor = HistoricalIngestor(
        db,
        FakeClient(),  # type: ignore[arg-type]
        blob_dir=tmp_path / "blobs",
        logger=lambda _: None,
    )
    ingestor.seed([("reports", "https://www.sec.gov/reports", "page")])

    result = ingestor.run()
    assert result.pages == 1
    assert result.documents == 1
    assert result.errors == 0

    archive = ArchiveStore(db)
    rows = archive.search_documents("investor protection")
    assert len(rows) == 1
    document = archive.get_document(rows[0]["id"])
    assert document is not None
    assert document["category"] == "speeches-statements"
    assert document["blob_path"]
    assert Path(document["blob_path"]).exists()
