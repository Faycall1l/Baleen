# Baleen — Architecture

Baleen is an agentic **Graph-RAG** system that turns a live financial-Twitter/X
stream (congressional trading disclosures + unusual options flow, in the style
of [@unusual_whales](https://x.com/unusual_whales)) into a queryable Neo4j
knowledge graph, exposed through an Obsidian-style graph UI, a natural-language
query bar, and a lightweight alert engine.

This document is the high-level architecture map. Start with the
[README](../README.md) for quickstart, then read this for the data flow, then
[SCHEMA.md](./SCHEMA.md) for the exact graph/table definitions.

---

## System overview

```
                          ┌──────────────────────────────┐
                          │        X (Twitter) API        │
                          │        recent-search v2       │
                          └──────────────┬───────────────┘
                                         │ poll (APScheduler)
                                         ▼
        ┌─────────────────────────────────────────────────┐
        │                   BACKEND (FastAPI)              │
        │                                                 │
        │  INGESTION         raw_tweets  ─────────────► PostgreSQL
        │  (tweepy, dedup)      (tweet_id UNIQUE)          ▲
        │                                                 │
        │  EXTRACTION         vLLM / OpenAI-compat         │
        │  (tool-calling or   /v1/chat/completions         │
        │   constrained-JSON)  ────────────► Event         │
        │                        (Pydantic validated)      │
        │                                                 │
        │  GRAPH WRITER       Cypher MERGE ─────────────► Neo4j
        │  (idempotent)         nodes + edges + source     │
        │                        tweet provenance          │
        │                                                 │
        │  QUERY AGENT         NL question ──► Cypher      │
        │  (read-only role,     allowlist validate ──► run │
        │   allowlist)          ──► grounded NL answer     │
        │                                                 │
        │  ALERT ENGINE        rules + LLM summary ──────► alerts (PG)
        └─────────────────────────────────────────────────┘
                          ▲                 │
        ┌─────────────────┴─────┐   ┌───────▼───────────────┐
        │  FRONTEND (React+Vite)│   │                       │
        │  Graph View           │   │  /api/graph           │
        │  Query Bar            │   │  /api/query           │
        │  Alerts Panel         │   │  /api/alerts          │
        └───────────────────────┘   └───────────────────────┘
```

## Repository layout

```
Baleen/
├── docker-compose.yml        # Postgres + Neo4j + backend + frontend
├── .env.example              # required secrets (copy to .env)
├── README.md
├── docs/
│   ├── ARCHITECTURE.md       # this file
│   ├── ASSUMPTIONS.md        # decisions made where the spec was missing
│   ├── SCHEMA.md             # exact Neo4j schema + Postgres tables
│   ├── SETUP.md              # env vars, vLLM wiring, seeding, tests
│   └── ALERTS.md             # alert engine + the one MVP rule
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app + scheduler startup
│   │   ├── config.py         # pydantic-settings, all env vars
│   │   ├── models/           # Pydantic schemas (events)
│   │   ├── db/               # Postgres + Neo4j clients
│   │   ├── ingestion/        # TweetSource interface, X + disclosure + Clerk +
│   │                         #   options-radar impls, MultiSource fan-out
│   │   ├── extraction/       # LLM client wrapper + extractor
│   │   ├── graph/            # Neo4j schema constants + MERGE writer
│   │   ├── query/            # Cypher allowlist validator + NL→Cypher agent
│   │   ├── alerts/           # rule evaluation + alert persistence
│   │   ├── reports/          # published briefs + digests
│   │   ├── api/              # REST routes (/graph, /query, /alerts, /reports)
│   │   └── scheduler.py      # APScheduler wiring (polls all live sources)
│   ├── scripts/              # seed_reference.py, ingest_once.py (--real), llm_mock.py
│   └── tests/                # per-phase unit + integration tests
└── frontend/
    └── src/
        ├── App.tsx           # four surfaces: Graph, Query, Alerts, Reports
        └── components/       # GraphView, QueryBar, AlertsPanel, ReportsPanel, NodeDetail
```

## Data flow phases

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
For each new `raw_tweet`, the extractor asks the configured OpenAI-compatible
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
`backend/app/alerts/engine.py` runs on every new `CongressTradeEvent`. It
evaluates active rules from the `alert_rules` table. The single MVP rule —
`politician_trades_in_oversight_sector` — fires when a politician trades a
ticker whose sector is overseen by one of their committees. On trigger the LLM
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

Four surfaces, no dashboards, no extra pages:

- **Graph view** — force-directed rendering of the Neo4j graph fetched from
  `/api/graph`, dark Obsidian theme, click-to-expand node detail panel showing
  linked source tweets.
- **Query bar** — always visible; calls `/api/query`; shows the generated
  (allowlisted) Cypher and answers grounded in the result rows, citing source
  tweets.
- **Alerts panel** — collapsible; lists recent alerts with links to relevant
  graph nodes.
- **Reports panel** — auto-published event briefs (one per typed event) plus
  the rolling daily digest, from `/api/reports`.

## Deployment

Docker Compose runs four services from `docker-compose.yml`: Postgres, Neo4j,
the FastAPI backend, and the Vite frontend (served as a static build behind the
backend, or as its own container). One `docker-compose up` from a clean checkout
starts everything. See [SETUP.md](./SETUP.md).
