# sunburst

The Stellar Stallion — and an SEC public-comment index built for MCP.

Sunburst discovers SEC public-comment pages, downloads the linked submissions, extracts searchable text, stores the corpus in SQLite/FTS5, and exposes it through a Model Context Protocol server.

## What it ingests

The crawler starts from the SEC's Commission rulemaking index, SRO rulemaking index, PCAOB rulemaking index, rulemaking-petition index, current public-comment submission index, and legacy regulatory-actions index. It follows SEC rule/detail pages into both current public-comment pages (`/rules-regulations/public-comments/...`) and legacy comment indexes (`/comments/.../*.shtml`). From those indexes it captures the comment metadata and linked PDF, HTML, TXT, DOCX, and legacy DOC documents.

PDF, HTML, TXT, and DOCX bodies are extracted and indexed. Legacy binary `.doc` files are retained in the database with their metadata and source URL but currently record an extraction error rather than searchable body text.

Because the SEC's indexes and site structure evolve, `sunburst ingest --seed ...` can add any SEC page as another discovery root without changing the crawler.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Sunburst uses the current MCP Python SDK v2 and requires Python 3.11+.

## SEC fair-access identity

The SEC asks automated clients to declare a user agent and currently limits automated access to at most 10 requests per second. Sunburst therefore refuses to crawl without an identity and defaults to 4 requests/second.

```bash
export SUNBURST_USER_AGENT='Your Name your-email@example.com'
```

Use a real monitored contact address. You can lower the crawl rate with `--rps`; Sunburst rejects values over 9.

## Build or refresh the index

```bash
sunburst --db ./data/sec-comments.db ingest
```

The crawl queue lives in the same SQLite database. Stopping the process is safe; running the command again resumes pending work. Re-fetching a previously seen document updates its stored record by canonical source URL rather than adding a duplicate.

For a small smoke run:

```bash
sunburst --db ./data/sec-comments.db ingest --max-items 50
```

Add an extra SEC discovery root:

```bash
sunburst --db ./data/sec-comments.db ingest \
  --seed 'https://www.sec.gov/comments/s7-27-15/s72715.shtml'
```

Search locally:

```bash
sunburst --db ./data/sec-comments.db search 'transfer agent' --file-number S7-27-15
sunburst --db ./data/sec-comments.db stats
```

## Run the MCP server

```bash
export SUNBURST_DB="$PWD/data/sec-comments.db"
sunburst-mcp
```

Or use the MCP CLI/Inspector:

```bash
mcp dev sunburst/server.py
```

The server exposes four tools:

- `search_comments` — FTS5 search with optional file-number, commenter, and letter-type filters
- `get_comment` — full extracted text and metadata for one result
- `list_files` — indexed SEC file numbers and comment counts
- `index_stats` — corpus and crawl-queue statistics

A typical host configuration launches `sunburst-mcp` over stdio and sets `SUNBURST_DB` to the populated database.

## Database

SQLite is intentionally the first storage backend: the database file is portable, resumable, easy to back up, and FTS5 provides useful full-text search without an external service. The ingestion layer and MCP layer are separated so a later PostgreSQL/OpenSearch backend can replace `sunburst/db.py` without rewriting SEC crawling.

The core tables are:

- `comment_pages` — SEC comment-list metadata
- `comments` — one row per linked submission, with extracted text and SHA-256
- `comments_fts` — FTS5 search index maintained by triggers
- `crawl_queue` — persistent page/document discovery state

## Tests

```bash
pytest
```

Tests are local fixtures only; they do not hit SEC.gov.
