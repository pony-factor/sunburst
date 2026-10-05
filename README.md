# sunburst

The Stellar Stallion — an SEC public-comment and historical-document archive built for MCP.

Sunburst discovers SEC material, preserves original historical files, extracts searchable text, stores metadata and indexes in SQLite/FTS5, and exposes the corpus through a Model Context Protocol server.

## Corpus

Sunburst has two complementary ingestion pipelines.

### Public comments

The comment crawler starts from Commission rulemaking, SRO rulemaking, PCAOB rulemaking, rulemaking-petition, current public-comment, and legacy regulatory-action indexes. It follows both current public-comment pages (`/rules-regulations/public-comments/...`) and legacy comment indexes (`/comments/.../*.shtml`).

It records file numbers, received dates, letter types, commenters, source URLs, hashes, media types, and extracted body text.

### Historical SEC documents

`ingest-history` crawls SEC-hosted archival families including:

- reports and publications, including annual reports
- speeches, testimony, and public statements
- press releases
- litigation releases
- administrative proceedings and archived case materials
- ALJ initial decisions
- Commission opinions and adjudicatory orders
- trading suspensions
- Accounting and Auditing Enforcement Releases
- policy statements
- Staff Legal Bulletins and Staff Accounting Bulletins
- broader staff guidance
- Corporation Finance no-action, interpretive, and exemptive letters
- rules and regulatory releases
- configured SEC manuals

Historical documents retain searchable metadata plus a content-addressed local copy of the original fetched bytes. See [Historical SEC coverage](docs/historical-coverage.md) for the configured roots, historical reach, crawl boundaries, and known gaps in the SEC's online holdings.

Sunburst deliberately does **not** crawl the EDGAR issuer-filing universe as part of historical mode.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Sunburst requires Python 3.11+ and uses MCP Python SDK v2.

## SEC fair-access identity

Automated SEC requests need a declared identity. Sunburst refuses to crawl without one and defaults to 4 requests/second, with a hard application limit of 9 requests/second.

```bash
export SUNBURST_USER_AGENT='Your Name your-email@example.com'
```

Use a real monitored contact address.

## Ingest public comments

```bash
sunburst --db ./data/sec.db ingest
```

A small smoke run:

```bash
sunburst --db ./data/sec.db ingest --max-items 50
```

Add a comment discovery root:

```bash
sunburst --db ./data/sec.db ingest \
  --seed 'https://www.sec.gov/comments/s7-27-15/s72715.shtml'
```

## Ingest historical SEC material

```bash
sunburst --db ./data/sec.db ingest-history
```

By default, original historical files are stored under a `blobs/` directory next to the database. Override that location with either:

```bash
export SUNBURST_BLOB_DIR=/srv/sunburst/sec-blobs
```

or:

```bash
sunburst --db ./data/sec.db ingest-history \
  --blob-dir ./data/sec-blobs
```

The archive queue is persistent and resumable. Stop the crawler at any time and run the same command again to continue pending work.

For a bounded crawl:

```bash
sunburst --db ./data/sec.db ingest-history --max-items 100
```

Add another SEC historical collection:

```bash
sunburst --db ./data/sec.db ingest-history \
  --seed 'special-study=https://www.sec.gov/path/to/archive'
```

Use `--only-seeds` when you want to crawl only the supplied historical roots.

## Search locally

Comments:

```bash
sunburst --db ./data/sec.db search 'transfer agent' \
  --file-number S7-27-15
```

Historical documents:

```bash
sunburst --db ./data/sec.db search-history 'market structure' \
  --category speeches-statements \
  --year 1975
```

Filter historical results by release or file number:

```bash
sunburst --db ./data/sec.db search-history 'broker dealer' \
  --release-number 34- \
  --file-number 3-
```

Search both corpora:

```bash
sunburst --db ./data/sec.db search-all 'direct registration'
```

Corpus and crawl statistics:

```bash
sunburst --db ./data/sec.db stats
```

## MCP server

```bash
export SUNBURST_DB="$PWD/data/sec.db"
sunburst-mcp
```

Or open it in the MCP Inspector:

```bash
mcp dev sunburst/server.py
```

The MCP server exposes:

- `search_comments` — full-text public-comment search with comment-specific filters
- `get_comment` — complete extracted text and metadata for one comment
- `list_files` — comment counts grouped by SEC file number
- `search_historical_documents` — full-text historical search with category, year, release-number, and file-number filters
- `get_historical_document` — complete historical text, metadata, SEC source URL, digest, and stored original path
- `list_historical_categories` — category counts and indexed date ranges
- `search_sec_corpus` — one request across comments and historical documents
- `index_stats` — both corpus sizes and both resumable queue states

A research client can use search results to identify relevant material, fetch the full stored text with the corresponding getter, and retain the SEC source URL and raw-blob reference for source verification.

## Storage model

SQLite is the catalog and search index. Raw historical files are stored separately in content-addressed blob storage so the database does not need to duplicate large PDFs and other binaries.

Core comment tables:

- `comment_pages`
- `comments`
- `comments_fts`
- `crawl_queue`

Core historical tables:

- `sec_documents`
- `sec_documents_fts`
- `archive_queue`

Historical rows contain category, title, normalized date when recoverable, author, release number, file number, parent/source URLs, media type, SHA-256, extracted text, blob path, fetch time, and extraction errors.

PDF, HTML, TXT, and DOCX text extraction is supported. Files that cannot yet be converted remain preserved as original blobs with metadata and an extraction error rather than being discarded.

## Tests

```bash
pytest
```

The fixture suite does not contact SEC.gov. GitHub Actions runs it on pushes to `main` and pull requests.
