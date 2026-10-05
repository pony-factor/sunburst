# Historical SEC coverage

Sunburst's historical archive mode is designed to preserve and index SEC-hosted regulatory history that is available online. It intentionally excludes the EDGAR issuer-filing corpus, which is a separate dataset with different scale and semantics.

## Built-in archive families

| Category | Built-in SEC root | Historical reach visible from the SEC index |
| --- | --- | --- |
| Reports and publications | https://www.sec.gov/reports?order=field_publish_date&sort=asc | Annual reports from 1935 and other Commission publications |
| Speeches and statements | https://www.sec.gov/newsroom/speeches-statements/speeches-statements-archive?order=field_publish_date&sort=asc | Archive includes material from 1929 and the pre-Commission 1933 Securities Act period |
| Press releases | https://www.sec.gov/newsroom/press-releases?order=field_publish_date&sort=asc | SEC press-release archive exposed by the current site |
| Litigation releases | https://www.sec.gov/enforcement-litigation/litigation-releases?order=field_publish_date&sort=asc | Online releases from 1995 forward |
| Administrative proceedings | https://www.sec.gov/enforcement-litigation/administrative-proceedings?order=field_publish_date&sort=asc | Orders and related administrative releases |
| ALJ initial decisions | https://www.sec.gov/enforcement-litigation/administrative-law-judges-decisions?order=field_publish_date&sort=asc | Online decisions from 1960 forward |
| Commission opinions and adjudicatory orders | https://www.sec.gov/enforcement-litigation/opinions-adjudicatory-orders?order=field_publish_date&sort=asc | Commission opinions and orders plus linked case material |
| Administrative case materials | https://www.sec.gov/litigation/apdocuments | Open and archived proceeding documents exposed online |
| Trading suspensions | https://www.sec.gov/enforcement-litigation/trading-suspensions?order=field_publish_date&sort=asc | Online series from 1995 forward |
| Accounting and Auditing Enforcement Releases | https://www.sec.gov/enforcement-litigation/accounting-auditing-enforcement-releases?order=field_publish_date&sort=asc | Current and legacy AAER archive pages |
| Policy statements | https://www.sec.gov/rules-regulations/policy-statements | Commission policy statements and linked releases |
| Staff guidance | https://www.sec.gov/rules-regulations/staff-guidance | Staff interpretations and guidance hubs |
| Staff Legal Bulletins | https://www.sec.gov/rules-regulations/staff-guidance/staff-legal-bulletins | SLB series and linked documents |
| Staff Accounting Bulletins | https://www.sec.gov/rules-regulations/staff-guidance/staff-accounting-bulletins | SAB series and codification material |
| Corporation Finance no-action, interpretive, and exemptive letters | https://www.sec.gov/divisions/corpfin/cfnew/cf-noaction.shtml | Online index principally covers letters after January 15, 2002 |
| Rules and regulatory releases | https://www.sec.gov/files/rules.shtml | Current and legacy rulemaking indexes and linked releases |
| Manuals | https://www.sec.gov/file/enforcementmanual | SEC manuals and linked source files reachable from configured roots |

## Preservation model

Every historical document is keyed by its canonical SEC URL and records:

- archive category
- title, date, author, release number, and file number when recoverable
- source and parent URLs
- media type
- SHA-256 digest
- full extracted text when the format is supported
- extraction errors when the original cannot yet be converted to text
- a content-addressed local path to the original fetched bytes

Original files are stored under a SHA-256 prefix directory so identical content is not repeatedly written under different human filenames.

## Crawl boundaries

The crawler follows only SEC-hosted archive families and directly linked SEC files. It rejects EDGAR issuer filing paths such as `/Archives/edgar`, the inline XBRL viewer, and EDGAR browse endpoints.

This is an online-archive completeness target, not a claim that every document the SEC has ever created is publicly available on SEC.gov. The SEC's own legacy pages identify important gaps. For example, the litigation archive notes that the SEC website was established on September 28, 1995 and directs users elsewhere for earlier litigation releases. The Corporation Finance no-action index says it principally contains letters dated after January 15, 2002 and that older letters may need to be requested separately.

When an offline or newly discovered SEC collection becomes available, add it as an explicit seed:

```bash
sunburst --db ./data/sec.db ingest-history \
  --seed 'collection-name=https://www.sec.gov/path/to/archive'
```

Using explicit roots keeps the historical scope reviewable and prevents an accidental crawl of unrelated SEC-hosted datasets.

## Search model

Historical text is indexed in SQLite FTS5 across title, category, author, release number, file number, and extracted body text. MCP clients can search the historical archive alone or search the public-comment and historical corpora together.

The original source URL and stored blob path remain attached to each result so a research agent can move from a search hit to the complete SEC source document.
