# Baleen — 5-Minute Live Demo

**One-liner:** Baleen turns live congressional trading disclosures and unusual
options flow into a queryable knowledge graph — then answers questions,
fires oversight alerts, and publishes reports about it. No API keys for data.

## Setup (60 seconds)

```bash
docker compose up -d postgres neo4j
cd backend && python -m scripts.ingest_once --real --seed-ref
```

The scheduler (backend, `SCHEDULER_ENABLED=true`) then keeps everything fresh
on its own every 5 minutes. UI at http://localhost:5173, API at :8000.

## Act 1 — The graph (60 seconds)

Open the UI. ~600 nodes / ~700 edges: politicians, committees, sectors,
tickers, and events — every node traceable to its source filing or quote
page. Click any `Event` node: the detail panel links the primary source.

Say: *"Every fact here came from a public filing or a public options chain
in the last few days — no scrapers, no keys, no tweets required."*

## Act 2 — Ask it questions (90 seconds)

Type into the query bar (real answers from the live graph):

1. *"Which tickers did congressional traders buy most recently?"*
2. *"What sectors does the House Financial Services Committee oversee, and
   which of those sectors have tickers in the graph?"*
3. *"Show unusual options flow in the last 24 hours by sector."*

Point at the generated Cypher under each answer: allowlisted, read-only,
executed against the graph — never freeform. If there's no data, it says so
instead of guessing.

## Act 3 — Alerts & reports (60 seconds)

- **Alerts panel:** fires when a politician trades a ticker in a sector their
  own committee oversees (e.g. *"Bill Cassidy traded UNH in Healthcare"*).
- **Reports panel:** every event gets an auto-published brief with source
  links, plus a daily digest (`/api/reports`, `/api/reports/digest`).

Say: *"Summaries are drafted strictly from graph facts, with deterministic
fallbacks — publishing never invents a trade."*

## Act 4 — Prove it's live (60 seconds)

```bash
cd backend && python -m scripts.ingest_once --real   # fresh filings in
curl localhost:8000/api/reports/digest | jq .        # fresh digest out
```

Or tail the backend log and watch the 5-minute tick ingest → extract →
graph → alert → report with zero manual steps.

## Numbers to quote

| Metric | Value |
|---|---|
| Ingestion sources | 3 keyless live feeds (disclosure mirror, Clerk-direct PDFs, CBOE chains) + X when keyed |
| Graph | ~600 nodes / ~700 edges, fully source-traceable |
| Reports published | auto-brief per event + daily digest |
| Tests | 167 passing (unit + live integration) |
| LLM calls | one OpenAI-compatible client; works against any `/v1` endpoint |

## If they ask…

- *"Where's the data from?"* — U.S. House/Senate STOCK Act filings and CBOE
  delayed chains. Public domain / public quotes. Show any event's source link.
- *"What if the LLM is wrong?"* — Filing facts (ticker, dates, amounts,
  party/chamber) overwrite model guesses deterministically; committees come
  from a curated roster, never the model; low-confidence output becomes
  `UnparsedEvent`, never a false fact.
- *"Can I run it?"* — `docker compose up`, point `LLM_BASE_URL` at any
  OpenAI-compatible endpoint, `ingest_once --real`. That's the whole setup.
