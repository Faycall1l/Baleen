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
│   │   ├── ingestion/        # TweetSource interface, X API impl, pipeline
│   │   ├── extraction/       # LLM client wrapper + extractor
│   │   ├── graph/            # Neo4j schema constants + MERGE writer
│   │   ├── query/            # Cypher allowlist validator + NL→Cypher agent
│   │   ├── alerts/           # rule evaluation + alert persistence
│   │   ├── api/              # REST routes (/graph, /query, /alerts)
│   │   └── scheduler.py      # APScheduler wiring
│   ├── scripts/              # seed_reference.py, ingest_once.py, llm_mock.py
│   └── tests/                # per-phase unit + integration tests
└── frontend/
    └── src/
        ├── App.tsx           # three surfaces only: Graph, Query, Alerts
        └── components/       # GraphView, QueryBar, AlertsPanel, NodeDetail
```

## Data flow phases

### 1. Ingestion
`backend/app/ingestion/pipeline.py` runs on a timer (APScheduler). It polls the
X API v2 `recent-search` endpoint for configured handles, normalizes tweets, and
inserts them into the Postgres `raw_tweets` table. Dedup is guaranteed by a
**unique constraint on `tweet_id`** — re-running any poll is a no-op for
already-seen tweets. Retry/backoff wraps API calls; every fetch is logged with
count, timestamp, and errors. `TweetSource` is an interface so the X
implementation can be swapped or mocked in tests.

### 2. Extraction
For each new `raw_tweet`, the extractor asks the locally-hosted LLM to classify
the text into one of the supported event types **using tool/function calling**
against Pydantic JSON schemas — never freeform JSON from prose. If the served
model has no native tool support, a constrained-JSON fallback is used: the
schema is embedded in the system prompt, output is strictly parsed, and invalid
output triggers a bounded retry. Every extraction carries a `confidence`
score; below `CONFIDENCE_THRESHOLD` (0.6) the tweet is stored as an
`UnparsedEvent` — never force-fit into the wrong schema.

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
5. The LLM writes a grounded answer, citing source tweet URLs from the graph.

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

## Frontend

Three surfaces only (no dashboards, no extra pages):

- **Graph view** — force-directed rendering of the Neo4j graph fetched from
  `/api/graph`, dark Obsidian theme, click-to-expand node detail panel showing
  linked source tweets.
- **Query bar** — always visible; calls `/api/query`; highlights the subgraph
  used in the answer.
- **Alerts panel** — collapsible; lists recent alerts with links to relevant
  graph nodes.

## Deployment

Docker Compose runs four services from `docker-compose.yml`: Postgres, Neo4j,
the FastAPI backend, and the Vite frontend (served as a static build behind the
backend, or as its own container). One `docker-compose up` from a clean checkout
starts everything. See [SETUP.md](./SETUP.md).
