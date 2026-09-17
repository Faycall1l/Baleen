# Baleen — Setup Guide

How to configure, run, seed, and test Baleen.

## Prerequisites

- Docker + Docker Compose (v2)
- Python 3.11+ (only for running the backend/tests outside Docker)
- Node.js 18+ (only for frontend dev outside Docker)
- An X (Twitter) API v2 developer project — or placeholder creds, see below
- A vLLM instance serving an open-weight model (or a local mock, see below)

## 1. Configure environment

```bash
cp .env.example .env
```

The backend loads this repo-root `.env` file automatically regardless of the
working directory it is launched from, so you can run uvicorn / scripts from
`backend/` and still pick up the same configuration.

Fill in real values (see [`.env.example`](../.env.example) for inline docs):

| Variable | Required | Notes |
| --- | --- | --- |
| `X_API_BEARER_TOKEN` | for live ingestion | X API v2 bearer token. Development/testing can use placeholders — the pipeline just logs fetch errors. |
| `LLM_BASE_URL` | dev/test only | vLLM's OpenAI-compatible endpoint, e.g. `http://host.docker.internal:8001/v1`. Placeholders are fine for tests (they use a stub). |
| `LLM_API_KEY` | dev/test only | vLLM is usually unauthenticated on localhost; any value works. |
| `LLM_MODEL_NAME` | dev/test only | model served by vLLM. |
| `LLM_TOOL_MODE` | no | `auto` (default) \| `tools` \| `json`. See §LLM below. |
| `POSTGRES_*` / `DATABASE_URL` | no | defaults match `docker-compose.yml`. |
| `NEO4J_*` | no | defaults match `docker-compose.yml`. |

## 2. Pointing Baleen at the real vLLM instance

The LLM client is a thin wrapper in
`backend/app/extraction/client.py`. Everything is driven by the three env vars
above — no provider SDK is hardcoded.

1. Start vLLM serving your model, e.g.:
   ```bash
   vllm serve <model> --port 8001
   ```
2. Set `LLM_BASE_URL=http://host.docker.internal:8001/v1` in `.env`
   (if vLLM runs on the host and Baleen in Docker).
3. Set `LLM_MODEL_NAME` to the model served.

### Native tool-calling vs constrained-JSON fallback

`LLM_TOOL_MODE=auto` (default) probes the served model:

- **Native tools** — the wrapper sends `tools` in the chat-completions payload
  and parses `tool_calls`. Used when the model supports it.
- **Constrained-JSON fallback** — if the model errors on `tools` or returns no
  `tool_calls`, the wrapper retries with the JSON schema embedded in the system
  prompt and strictly parses/validates the reply (bounded retries). This keeps
  structured extraction reliable on models without native tool support.

`tools` and `json` force either mode. See `docs/ARCHITECTURE.md` for details.

Transport failures (network blips, a vLLM restart, HTTP error statuses) are
retried with exponential backoff up to `LLM_MAX_RETRIES` (default 2), so a
single hiccup never fails a whole run; `LLM_TIMEOUT_SECONDS` bounds each
attempt.

### Local mock server (no LLM needed)

For development and tests without any model, run the bundled stub — it serves
tool calls / JSON extraction and query-answering with deterministic canned
output:

```bash
cd backend && python -m scripts.llm_mock   # listens on :8001 (set PORT=8002 to override)
```

Point Baleen at it with `LLM_BASE_URL=http://localhost:8001/v1` (or the port you
chose). Tests use an in-process stub, so they don't require the mock to be
running.

## 3. Run everything

```bash
docker-compose up --build
```

This starts: Postgres (:5432), Neo4j (:7474, :7687), backend (:8000),
frontend (:5173).

Open http://localhost:5173. FastAPI docs at http://localhost:8000/docs.

## 4. Seed reference data

Politicians, committee memberships, and committee→sector oversight mappings
live in `backend/scripts/seed_data.py` and are loaded into the graph with:

```bash
# inside the backend container
docker-compose exec backend python -m scripts.seed_reference
```

The seed is idempotent (`MERGE`) — safe to run repeatedly.

## 5. Ingest a batch of tweets

```bash
docker-compose exec backend python -m scripts.ingest_once
```

Polls the configured handles once (not on a timer), runs extraction, writes to
the graph, and evaluates alerts. Useful for smoke tests and for hand-inserting
example tweets. With no X API credentials it falls back to a bundled sample
corpus so the full stack can be demoed end-to-end:

```bash
docker-compose exec backend python -m scripts.ingest_once --seed-ref
```

`--seed-ref` seeds the reference graph (politicians/committees/sectors) first —
needed when the graph is empty.

### Live ingestion (real X API + vLLM)

Once `X_API_BEARER_TOKEN`, `LLM_BASE_URL`, and `LLM_MODEL_NAME` are set in
`.env`, pull a batch from the real API and run it through the stack:

```bash
python -m scripts.ingest_once --live
```

`ingest_once --live` fails fast if the token is missing or still a placeholder,
and the scheduler (if enabled) picks up the same X API source on its timer.
Ticks that fail (rate limits, transient errors) back off exponentially up to
10 minutes and recover automatically. If the LLM endpoint is down during a tick,
the affected tweets are *not* marked unparsed — they stay pending and are
re-processed automatically once the endpoint recovers.

Each poll walks up to `X_MAX_PAGES` (default 3) result pages of 100 tweets via
the `next_token` cursor, deduplicating across pages, so a very active handle
cannot starve the others in the query. Raise it to backfill a longer window;
the API caps it at 10. Every X API call is bounded by
`X_API_TIMEOUT_SECONDS` (default 30) — tweepy never sets a timeout of its own,
so without this a hung endpoint would stall the scheduler tick indefinitely.

### Real public feeds (no X key needed)

Without X access, poll the live keyless feeds instead — real data, real time:

```bash
python -m scripts.ingest_once --real
```

This runs the full stack over two sources (combined with `MultiSource`, one
failing feed never aborts the other):

- **Congressional disclosures** — normalized House/Senate PTR filings (public
  STOCK Act data, refreshed daily). Each trade links to its official filing
  PDF. The pipeline deterministically grounds ticker, dates, amounts,
  party/chamber from the filing and resolves identity/committees from the
  reference roster — the LLM contributes classification + confidence, never
  hallucinated identifiers. Unknown filers get a stable slug id and *no*
  committees, so alerts only fire on verified memberships.
- **Options radar** — delayed CBOE chains (~15 min) for a liquid watchlist;
  contracts with day volume ≥ 2× open interest (≥ 500 absolute, top-3 per
  ticker) are emitted as flow reports labeled delayed.

The scheduler polls the same feeds every `POLL_INTERVAL_SECONDS` (default
300s) and drains new records through extract → graph → alerts, so the graph
stays current without any manual runs. Re-polls re-emit recent rows and
downstream dedup by id drops them — safe to run on a tight timer.

Real-model notes (learned against a live vLLM serving Gemma):

- Point `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL_NAME` at the endpoint; the
  `/models` health probe sends the key, so auth-gated servers report healthy.
- If the endpoint has no tool-call parser, set `LLM_TOOL_MODE=json` to skip
  the wasted native-tools round trip per extraction.
- The constrained-JSON parser tolerates the envelope shapes real models emit
  (`{"name", "arguments"}`, `{"tool", "parameters"}`, flat) — valid
  extractions don't become unparsed on envelope variance.

### Re-running tweets that failed to parse

If a tweet produced only an `UnparsedEvent`, re-queue it for extraction (e.g.
after a better model or a fixed extractor is available):

```bash
python -m scripts.retry_unparsed              # reset genuinely-unparsed tweets to 'new'
python -m scripts.retry_unparsed --dry-run    # preview first
python -m scripts.retry_unparsed --process    # reset, then run the pipeline immediately
```

Tweets that *later* produced a typed event keep only that event — their stale
`UnparsedEvent` nodes are purged automatically (obsolete records).

## 6. Neo4j read-only role (query agent)

The query agent must run against a **read-only** Neo4j user so it can never
mutate the graph. Create the role once:

```cypher
-- run as the admin user, e.g. in Neo4j Browser at :7474
CREATE ROLE baleen_reader;
GRANT ACCESS ON DATABASE neo4j TO baleen_reader;
GRANT MATCH {*} ON GRAPH neo4j ELEMENTS TO baleen_reader;
GRANT TRAVERSE ON GRAPH neo4j ELEMENTS TO baleen_reader;
GRANT READ {*} ON GRAPH neo4j PROPERTIES TO baleen_reader;
CREATE USER baleen_reader SET PASSWORD 'changeme';
GRANT ROLE baleen_reader TO baleen_reader;
```

Set the agent's credentials via `NEO4J_READ_URI` / `NEO4J_READ_USER` /
`NEO4J_READ_PASSWORD`. Until `NEO4J_READ_URI` is configured, read queries fall
back to the writer driver (fine for local dev — the real guardrail is the Cypher
allowlist); once the role above exists, pin `NEO4J_READ_URI`/`NEO4J_READ_USER`/
`NEO4J_READ_PASSWORD` in `.env` to enforce defense-in-depth.

### Query timeout guard

Every Cypher statement runs inside a transaction with a server-enforced timeout
(`NEO4J_QUERY_TIMEOUT_SECONDS`, default 30; set `0` to disable). This protects
the API and the query agent from pathological queries hanging the stack.

### Preflight check (before a live run)

Before pointing Baleen at the real X API and vLLM, run the preflight script —
one command that validates every config value and dependency (X token, LLM
reachability incl. the `GET /models` probe, Postgres, Neo4j, scheduler) and
exits non-zero with a clear message when anything is missing or unreachable:

```bash
cd backend
.venv/bin/python -m scripts.preflight
```

The LLM probe also catches the classic mistake of leaving `LLM_BASE_URL`
pointing at a port that serves something other than vLLM (non-2xx `GET /models`
is reported as FAIL).

### Health endpoint

`GET /health` reports Postgres and Neo4j connectivity (503 when either is
down). Once `LLM_MODEL_NAME` is a real model (not a placeholder), it also
probes the LLM endpoint (`GET /models`) — useful during the live trial, since
the frontend health pill reflects it.

## 7. Run tests
```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Tests that need live Postgres/Neo4j are skipped when the services are down
(they check connectivity first). Run `docker-compose up postgres neo4j` to
exercise the integration tests, or bring up the full stack.

### Test Neo4j isolation
The entity-resolution suite never mutates the live graph the scheduler writes
to. Bring up the dedicated test instance with:

```bash
docker compose up -d neo4j-test   # bolt://localhost:7690
```

The test harness auto-detects it (a one-second socket probe) and promotes the
whole test process to it; without it, tests fall back to the regular `neo4j`
instance on 7687 so plain `pytest` keeps working out of the box.

### Resource budgeting
Both Neo4j containers run with bounded CPU/memory (`NEO4J_CPUS`/`NEO4J_MEM` for
the live graph, `NEO4J_TEST_CPUS`/`NEO4J_TEST_MEM` for the test instance) so a
co-tenanted host can't starve them. On a dedicated box, raise the env vars. The
app lays down a staggered set of Neo4j budgets: `NEO4J_QUERY_TIMEOUT_SECONDS`
(cap per statement), and `NEO4J_MERGE_TIMEOUT_SECONDS` (cumulative budget for
the single multi-statement identity-merge transaction).

### Measure performance (bench harness)
`scripts/bench.py` produces trusted, statistically-backed numbers even on the
saturated shared host (k3d/gmem co-tenancy). It replays a fixed synthetic
tweet batch (deterministic ids, `DeterministicLLM` stub so no external model
latency) through the real pipeline with warmup + adaptive sampling: rounds
continue until the bootstrap 95% CI width of **throughput (tweets/s)** is at
or below `--target-ci` (default 10%), bounded by `--min-rounds`/`--max-rounds`
(6/24) so one contended host can't balloon the run. It times each stage per
call (extract / graph write / alerts / reports / Neo4j read probe), logs the
host load average alongside, and reports median, p90/p99, CV and a bootstrap
CI — never a bare mean — plus a **load-adjusted throughput** (OLS of tps on
host `load1`, read off at `--ref-load`) so a busy neighbor isn't mistaken for
a regression.

```bash
python -m scripts.bench                          # adaptive (6-24 rounds), batch 8
python -m scripts.bench --rounds 20 --batch 8 --json /tmp/bench.json   # fixed rounds
python -m scripts.bench --min-rounds 12 --target-ci 0.05 --batch 16    # tighter, longer
```

Targets `baleen_test` (5433) and the isolated `neo4j-test` (7690) by default
and refuses a non-test DSN. For version-vs-version regressions, the **duet**
mode compares the current checkout and a baseline SHA in alternating rounds on
the same host (same instances, interleaved order) and reports the per-pair
ratio with a bootstrap CI and an exact sign test — the right tool when noisy
neighbors dominate absolute numbers:

```bash
python -m scripts.bench --aa --duet-pairs 6             # A/A false-positive calibration
python -m scripts.bench --duet <baseline-sha> --duet-pairs 6
```

The determined-LLM stub removes external model latency so the harness measures
Baleen's own stack; replace `DeterministicLLM` with a live endpoint if you
want end-to-end numbers including the model.

## 8. Troubleshooting

- **Backend can't reach Neo4j/Postgres** — ensure `docker-compose up` is running
  and the `.env` hostnames match the service names (`postgres`, `neo4j`).
- **LLM extraction returns `UnparsedEvent` for everything** — the model may not
  be responding; check backend logs, try `LLM_TOOL_MODE=json`, and confirm the
  mock/model is actually listening on `LLM_BASE_URL`.
- **Ports in use** — Baleen uses 5432, 7474, 7687, 7690 (test Neo4j), 8000,
  8001 (mock LLM), 5173. Stop other projects on these ports before
  `docker-compose up`; 7688 is deliberately avoided because the co-located
  gmem project binds it.

---

## 9. Published reports

Every typed event gets an auto-generated **brief** (deterministic headline +
LLM-drafted factual body grounded in the event's graph neighborhood, with
source links), written inline by the processing pipeline. A rolling **daily
digest** summarizes newly-arrived events. Both live in the `reports` table
(idempotent: one brief per event, one digest per day) and are served at:

```bash
curl localhost:8000/api/reports | jq .      # latest briefs + digests
curl localhost:8000/api/reports/digest | jq .  # today's digest (generated on first request)
```

The Reports panel in the UI polls the same endpoints live. If the LLM is down,
briefs/digests fall back to deterministic templates — publishing never blocks
the pipeline.

### Cleaning up orphaned events

Demo/test runs can leave `Event` nodes whose source tweet is no longer in
`raw_tweets`. Prune them so the graph only holds verifiable provenance
(reference nodes — politicians, committees, sectors, tickers — are untouched):

```bash
python -m scripts.prune_orphan_events --dry-run   # preview
python -m scripts.prune_orphan_events             # delete orphans
```
