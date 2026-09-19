<p align="center">
  <img src="frontend/public/baleen.svg" alt="Baleen logo" width="112" />
</p>

<h1 align="center">Baleen</h1>

<p align="center">
  Agentic Graph-RAG for the financial side of X/Twitter.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green" /></a>
  <a href="https://www.python.org/"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white" /></a>
  <a href="docker-compose.yml"><img alt="Docker Compose" src="https://img.shields.io/badge/docker-compose-2496ED?logo=docker&logoColor=white" /></a>
</p>

Baleen turns a live stream of congressional trading disclosures and unusual
options flow (in the style of [@unusual_whales](https://x.com/unusual_whales))
into a queryable **Neo4j knowledge graph** — then lets you explore it in an
Obsidian-style graph UI, ask questions in plain English, and get alerted when a
politician trades in a sector their own committee oversees.

```
          X API v2                     vLLM (open-weight LLM)
   raw tweets ──────────►  Postgres ──────────► typed events ──► Neo4j graph
                              │                    ▲                  │
                          dedup by tweet_id   structured extract   MERGE upserts
                                                (confidence ≥ 0.6)      │
                                                                        ▼
        frontend (React + force graph) ◄────────────────── NL query agent + alerts
```

## Highlights

- **Robust over feature-rich.** Narrow MVP scope, no feature creep. See [MVP scope](#mvp-scope).
- **Idempotent everywhere.** Raw tweets dedup on `tweet_id` (unique constraint);
  graph writes use `MERGE` on natural identifiers. Re-running any stage is a no-op.
- **Structured extraction only.** The LLM classifies tweets via tool-calling
  against Pydantic schemas — never freeform JSON from prose. Low-confidence
  output (< 0.6) is stored as `UnparsedEvent`, never force-fit.
- **Fully traceable.** Every node and edge points back to the source tweet URL.
- **Guardrailed query agent.** Generated Cypher is validated against an
  allowlist and runs on a read-only Neo4j role. No fabrication — if there's no
  data, it says so.
- **Bring your own LLM.** All LLM calls go through one thin client wrapper
  (`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME`) aimed at a local vLLM
  instance. Works with placeholders until real credentials arrive; native
  tool-calling with a constrained-JSON fallback for models without tool support.

## Architecture at a glance

```
┌─────────┐    ┌──────────────┐    ┌──────────────┐    ┌─────────────┐
│ Ingestion│ → │  Extraction  │ → │ Graph writer │ → │ Query agent │
│ (tweepy) │   │ (LLM + schemas)│  │  (Neo4j MERGE)│  │ (NL → Cypher)│
└─────────┘    └──────────────┘    └──────────────┘    └─────────────┘
     └──────────────► alerts engine ──► Postgres alerts table
```

Four UI surfaces, no dashboards: **Graph**, **Query Bar**, **Alerts Panel**, **Reports**
(auto-published event briefs + daily digest, also at `/api/reports`).

## Repository layout

```
Baleen/
├── docker-compose.yml        # Postgres + Neo4j + backend + frontend
├── .env.example              # required secrets (copy to .env)
├── docs/                     # ARCHITECTURE, SCHEMA, SETUP, ALERTS, ASSUMPTIONS
├── backend/                  # FastAPI: ingestion, extraction, graph, query, alerts
│   ├── app/
│   ├── scripts/              # seed_reference, ingest_once (--live), llm_mock,
│   │                         #   retry_unparsed, prune_orphan_events
│   └── tests/
└── frontend/                 # React + Vite: graph view, query bar, alerts panel
```

## Quickstart

1. **Configure**
   ```bash
   cp .env.example .env      # fill in X bearer token + LLM vars (see docs/SETUP.md)
   ```
2. **Run**
   ```bash
   docker-compose up --build
   ```
   → UI at http://localhost:5173 · API docs at http://localhost:8000/docs
3. **Seed reference data** (politicians, committees, oversight mappings)
   ```bash
   docker-compose exec backend python -m scripts.seed_reference
   ```
4. **Ingest a batch and confirm the pipeline**
   ```bash
   docker-compose exec backend python -m scripts.ingest_once
   ```

Full configuration, vLLM wiring, the Neo4j read-only role, and troubleshooting
live in [docs/SETUP.md](docs/SETUP.md).

### Local demo (no X API, no GPU)

Without credentials or a model, run the whole stack with the bundled mock:

```bash
# 1. services up
docker-compose up -d postgres neo4j

# 2. mock LLM (deterministic extraction + query answers)
cd backend && LLM_BASE_URL=http://localhost:8002/v1 PORT=8002 python -m scripts.llm_mock

# 3. seed the reference graph + ingest the sample corpus + run the pipeline
LLM_BASE_URL=http://localhost:8002/v1 python -m scripts.ingest_once --seed-ref

# 4. backend (points at the mock)
LLM_BASE_URL=http://localhost:8002/v1 SCHEDULER_ENABLED=false \
  python -m uvicorn app.main:app --port 8000

# 5. frontend
cd ../frontend && npm install && npm run dev   # → http://localhost:5173
```

The sample corpus (see `backend/app/ingestion/source.py`) includes an unusual
options sweep, a congressional trade that **triggers the alert rule**, and noise,
so all three panels — graph, query, alerts — are populated immediately.

## Ask the graph

Type a question in the query bar, e.g.:

- *"What sectors does the House Financial Services Committee oversee, and which of those sectors have tickers in the graph?"*
- *"Which tickers has Nancy Pelosi traded recently, and when?"*
- *"Show unusual options flow in the last 24 hours by sector."*

The agent answers grounded in the graph rows, citing the source tweets, and
shows the (allowlisted) Cypher it generated. If the graph has no data for a
question, it tells you instead of guessing.

## MVP scope

In scope: X API v2 polling ingestion; structured extraction of
`OptionsFlowEvent` / `CongressTradeEvent` / `UnparsedEvent`; Neo4j graph
writing; the allowlisted query agent; one alert rule
(`politician_trades_in_oversight_sector`); the three UI surfaces; Docker Compose
deployment.

Out of scope (deliberately): `DarkPoolEvent`, extra alert rules, dashboards,
additional UI pages, Kafka, and any scraping that bypasses the X API. Design
decisions made where the original spec file was unavailable are recorded in
[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md).

## Documentation

| Doc | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System overview, data flow, repo layout |
| [docs/SCHEMA.md](docs/SCHEMA.md) | Neo4j graph schema + Postgres tables (source of truth) |
| [docs/SETUP.md](docs/SETUP.md) | Env vars, vLLM wiring, seeding, tests, troubleshooting |
| [docs/ALERTS.md](docs/ALERTS.md) | Alert engine + the one MVP rule |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Decisions made where the original spec was unavailable |

## Development

```bash
# backend tests (Python 3.11+)
cd backend && pip install -r requirements-dev.txt && pytest -q

# frontend dev (needs backend running)
cd frontend && npm install && npm run dev
```

## Roadmap / stretch goals

- `DarkPoolEvent` extraction and graph wiring
- Additional alert rule types
- Historical backfill via timeline/history endpoints
- Streaming ingestion via X filtered-stream endpoint
- Vector embeddings on events for semantic graph search

## License

MIT — see [LICENSE](LICENSE).

---

> Baleen is a demonstration project. It is not financial advice, and it is not
> affiliated with X Corp. or @unusual_whales. Data shown is informational only.
