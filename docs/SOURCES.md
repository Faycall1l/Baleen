# Data sources, terms and attribution

Baleen reads public records. Nothing here is a data redistribution service:
the app stores normalized events, links every one back to its primary source,
and expects you to respect each provider's terms when you deploy it.

| Source | What it provides | Access | Terms / notes |
| --- | --- | --- | --- |
| **US House Clerk — financial disclosures** (`disclosures-clerk.house.gov`) | Periodic Transaction Reports (PTRs) as a yearly ZIP index plus per-filing PDFs | Keyless | US Government public records. Poll politely: the index changes daily, not by the minute. |
| **Congress trading mirror** (`kadoa-org/congress-trading-monitor`) | Normalized House + Senate PTR rows as JSON, refreshed daily | Keyless | MIT licensed. Used because it covers both chambers and exposes stable ids; the Clerk remains the authoritative record. Attribution belongs to that project. |
| **SEC EDGAR — Form 4** (`sec.gov`) | Insider transactions (officers, directors, 10% owners) | Keyless, requires a contact `User-Agent` | Public filings under the SEC's [fair access policy](https://www.sec.gov/os/webmaster-faq#developers). Set `EDGAR_USER_AGENT` to a real contact before sustained polling. |
| **CBOE delayed option chains** (`cdn.cboe.com`) | Delayed option volume and open interest for a watchlist | Keyless | Delayed data, CBOE terms apply. Fine for research and local use; confirm redistribution rights before publishing derived feeds commercially. |
| **Yahoo Finance** (`finance.yahoo.com`) | Quote page used only as a human-readable source link | Link only | Not scraped. No data is extracted from it. |
| **X / Twitter API v2** | Optional free-text flow reports from finance accounts | Paid bearer token | Subject to the X Developer Agreement. Off by default; without a token the source is inert. Tweet text is not redistributed. |

## What Baleen stores

- Raw source rows in Postgres (`raw_tweets`), keyed by a stable id so re-runs
  are idempotent.
- Typed events and graph nodes/edges in Neo4j, each carrying a source URL.
- Generated briefs and digests in Postgres, grounded in those events.

No credentials, private messages or non-public personal data are collected.

## Adding a source

A new source must:

1. Be publicly accessible without bypassing authentication or rate limits.
2. Permit automated access under its terms.
3. Supply a stable identifier and a canonical URL for every record.
4. Fail in isolation — one broken feed must not block the others.

See `backend/app/ingestion/` for the `TweetSource` interface and
[CONTRIBUTING.md](../CONTRIBUTING.md) for the review bar.
