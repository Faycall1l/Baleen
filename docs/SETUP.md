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

## 7. Run tests

```bash
cd backend
pip install -r requirements-dev.txt
pytest -q
```

Tests that need live Postgres/Neo4j are skipped when the services are down
(they check connectivity first). Run `docker-compose up postgres neo4j` to
exercise the integration tests, or bring up the full stack.

## 8. Troubleshooting

- **Backend can't reach Neo4j/Postgres** — ensure `docker-compose up` is running
  and the `.env` hostnames match the service names (`postgres`, `neo4j`).
- **LLM extraction returns `UnparsedEvent` for everything** — the model may not
  be responding; check backend logs, try `LLM_TOOL_MODE=json`, and confirm the
  mock/model is actually listening on `LLM_BASE_URL`.
- **Ports in use** — Baleen uses 5432, 7474, 7687, 8000, 8001 (mock LLM),
  5173. Stop other projects on these ports before `docker-compose up`.
