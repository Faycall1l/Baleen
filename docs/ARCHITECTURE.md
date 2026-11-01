# Architecture

Baleen ingests public trading disclosures into a Neo4j knowledge graph and
serves it through a tile workspace, a natural-language query bar, and alert and
report engines. Read [SCHEMA.md](./SCHEMA.md) for the exact graph and table
definitions and [SETUP.md](./SETUP.md) to run it.

## System overview

```
 House Clerk PTRs ─┐
 Senate mirror ────┤
 SEC EDGAR Form 4 ─┼─► MultiSource ─► Postgres raw_tweets (dedup on tweet_id)
 CBOE option chains┤                          │
 X API (optional) ─┘                          ▼
                              extraction: filing rows → events directly,
                              free text → LLM tool-calling (Pydantic schemas)
                                              │
                                              ▼
                              Neo4j graph (idempotent MERGE, source URL on every node)
                                              │
              ┌──────────────┬────────────────┼───────────────┬───────────────┐
              ▼              ▼                ▼               ▼               ▼
          alerts         reports          signals       query agent      graph / flow
        (3 rules)    (briefs + digest)  (no LLM)     (NL → Cypher)      REST + SSE
                                              │
                                              ▼
                                   React tile workspace
```

## Repository layout

```
Baleen/
├── docker-compose.yml        # Postgres + Neo4j + backend + frontend
├── .env.example              # required secrets (copy to .env)
├── README.md
├── docs/
│   ├── ARCHITECTURE.md       # this file
│   ├── SCHEMA.md             # exact Neo4j schema + Postgres tables
│   ├── SETUP.md              # env vars, vLLM wiring, seeding, tests
│   └── ALERTS.md             # alert engine and rules
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app + scheduler startup
│   │   ├── config.py         # pydantic-settings, all env vars
│   │   ├── models/           # Pydantic schemas (events)
│   │   ├── db/               # Postgres + Neo4j clients
│   │   ├── ingestion/        # TweetSource interface, X + disclosure + Clerk + news +
│   │                         #   options-radar impls, MultiSource fan-out
│   │   ├── extraction/       # LLM client wrapper + extractor
│   │   ├── graph/            # Neo4j schema constants + MERGE writer
│   │   ├── query/            # Cypher allowlist validator + NL→Cypher agent
│   │   ├── alerts/           # rule evaluation + alert persistence
│   │   ├── reports/          # published briefs + digests
│   │   ├── signals/          # computed pressure signals (no LLM)
│   │   ├── api/              # REST routes (/graph, /query, /alerts, /reports, /leaderboard, /signals, /stream)
│   │   └── scheduler.py      # APScheduler wiring (polls all live sources)
│   ├── scripts/              # seed_reference.py, ingest_once.py (--real)
│   └── tests/                # unit + integration tests (Postgres and Neo4j required for the latter)
└── frontend/
    └── src/
        ├── App.tsx           # workspace shell, URL state, polling
        ├── components/       # one component per tile plus detail cards
        ├── hooks/            # feeds, graph polling, inspector links
        └── tileState.ts      # split-tree layout model (pure functions)
```

## Data flow

> Naming note: `raw_tweets`, `tweet_id` and `tweet_url` date from the X-only prototype. They now hold
> a record of any source (filing PDF, Form 4, options print, post).

### 1. Ingestion
`backend/app/ingestion/pipeline.py` runs on a timer (APScheduler). Each tick
fans out through `MultiSource` across every live source — congressional PTR
filings (community mirror + Clerk-direct PDF parsing), the delayed-chain
options radar, and the X API v2 `recent-search` endpoint when a real bearer
token is configured (keyless feeds need no credentials) — normalizes records,
and inserts them into the Postgres `raw_tweets` table. Dedup is guaranteed by
a **unique constraint on `tweet_id`** — re-running any poll is a no-op for
already-seen records. Retry/backoff wraps API calls; one failing source never
aborts the others and every fetch is logged with count, timestamp, and errors.
`TweetSource` is the interface all sources (and test fakes) implement.

### 2. Extraction
Structured filing rows skip the model entirely: `app/extraction/filing.py`
builds trade events straight from `raw_json` (filer, ticker, side, shares,
price, dates), because spending an LLM call per parsed filing is what
exhausts free-tier backups. This is what makes the scheduler viable without
a paid model endpoint. Anything else — and any filing row missing minimum
facts — falls back to the LLM path below, which also covers freeform posts.
For each remaining `raw_tweet`, the extractor asks the configured OpenAI-compatible
LLM (remote vLLM, `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`) to
classify the text into one of the supported event types **using tool/function
calling** against Pydantic JSON schemas — never freeform JSON from prose. If
the served model has no native tool support, a constrained-JSON fallback is
used: the schema is embedded in the system prompt, output is strictly parsed
(tolerating the envelope shapes real models emit), and invalid output triggers
a bounded retry. Records from the disclosure feed are then deterministically
grounded in filing facts (ticker, dates, amounts, party/chamber) with identity
and committee memberships resolved from the reference roster — the LLM
contributes classification + confidence, never hallucinated identifiers. Every
extraction carries a `confidence` score; below `CONFIDENCE_THRESHOLD` (0.6)
the tweet is stored as an `UnparsedEvent` — never force-fit into the wrong
schema.

### 3. Graph writing
The graph writer upserts nodes and edges with Cypher `MERGE`, keyed on natural
identifiers, and stamps every node/edge with `tweet_id` + `tweet_url` so the
graph is fully traceable. The schema is fixed and documented in
[SCHEMA.md](./SCHEMA.md). Writing the same event twice produces no duplicates.

### 4. Query agent
`backend/app/query/agent.py` turns a natural-language question into Cypher:

1. The **fixed schema** (from `graph/schema.py`) is given to the LLM as context.
2. The LLM proposes Cypher via tool-calling (or the same constrained-JSON
   fallback).
3. `validate.py` enforces an **allowlist** of node labels and relationship
   types, and rejects any write clause or procedure.
4. The Cypher runs against Neo4j with a **read-only role**.
5. The LLM writes a grounded answer, citing source URLs from the graph rows
   (filing PDFs, quote pages, and tweets alike).

If a query returns zero rows, the agent answers "I don't have enough data"
rather than fabricating. If Cypher fails validation or the LLM is unsure, it
says so.

### 5. Alerts
`backend/app/alerts/engine.py` runs on every new event. It
evaluates active rules from the `alert_rules` table (see [ALERTS.md](./ALERTS.md)). On trigger the LLM
drafts a short factual summary with source links, stored in the `alerts` table
and served to the Alerts Panel.

### 6. Published reports
`backend/app/reports/engine.py` publishes a **brief** for every typed event
(deterministic headline + LLM-drafted body grounded in the event's graph
neighborhood, with source links) and a rolling **daily digest** of
newly-arrived events, both stored idempotently in the `reports` table and
served to the Reports panel via `/api/reports`. Deterministic template
fallbacks keep publishing alive through LLM outages without ever blocking the
pipeline.

### 7. Computed signals
`backend/app/signals/engine.py` derives pressure signals from stored facts
only — no LLM, no future-return claims: congress buying momentum per ticker,
insider cluster buys, bullish/bearish options volume, trades already beating
the market, uncovered sector bets, and shared-ticker politician similarity.
Served live at `/api/signals` to the Signals panel.

## Frontend

A tile workspace: each panel docks beside the others, splits, enlarges, and
persists in the URL so any view is a shareable link.

- **Insights**: KPI strip and charts (congress buy/sell, options call/put, excess return, movers).
- **Story leads**: ranked signals (off-committee trades, market-beating filings, congress flow).
- **Signal board**: time-aligned event lanes with a time scrubber and type filters.
- **Media watch**: keyless RSS/Atom headlines with deterministic entity mentions.
- **Flow tape**: filterable options and insider prints.
- **Query bar**: natural-language questions; shows the allowlisted Cypher and the source rows.
- **Alerts, Reports, Leaderboard, Activity**: feeds from the matching REST routes.
- **Path**: shortest connection between two entities in the graph.
- **Detail cards** for politicians, tickers and committees, linked from every row.

## Deployment

`docker compose up` starts Postgres, Neo4j, the FastAPI backend and the
frontend. See [SETUP.md](./SETUP.md).
