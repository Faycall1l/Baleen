# Baleen — Data Schema

This is the single source of truth for the Neo4j knowledge graph and the
PostgreSQL tables. The query agent receives this schema verbatim as context
(see [ARCHITECTURE.md](./ARCHITECTURE.md#query-agent)), so any change here must
be mirrored in `backend/app/graph/schema.py`.

---

## 1. PostgreSQL

### `raw_tweets`

One row per ingested tweet. **`tweet_id` is unique** — this is what makes
ingestion idempotent (re-polls skip rows that already exist).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | BIGSERIAL | PK |
| `tweet_id` | TEXT | **UNIQUE**, X tweet ID |
| `author_handle` | TEXT | e.g. `unusual_whales` |
| `author_id` | TEXT | X user ID |
| `text` | TEXT | full tweet text |
| `created_at` | TIMESTAMPTZ | tweet creation time |
| `url` | TEXT | canonical status URL |
| `raw_json` | JSONB | full payload from the API |
| `fetched_at` | TIMESTAMPTZ | when Baleen fetched it |
| `status` | TEXT | `new` → `extracted` → `written` (pipeline progress) |

### `alert_rules`

Active rule definitions. Exactly one MVP rule ships seeded:
`politician_trades_in_oversight_sector`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | SERIAL | PK |
| `name` | TEXT | **UNIQUE**, rule identifier |
| `description` | TEXT | human-readable |
| `config` | JSONB | rule-specific config (currently `{}`) |
| `active` | BOOLEAN | only active rules are evaluated |

### `alerts`

Triggered alerts, surfaced to the Alerts Panel.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | SERIAL | PK |
| `rule_id` | INTEGER | FK → `alert_rules.id` |
| `event_id` | TEXT | source `Event.id` in the graph |
| `tweet_id` | TEXT | source tweet |
| `tweet_url` | TEXT | source tweet URL |
| `title` | TEXT | short headline |
| `summary` | TEXT | 2–3 sentence LLM-drafted summary |
| `graph_refs` | JSONB | node ids highlighted in the graph |
| `created_at` | TIMESTAMPTZ | |

---

## 2. Neo4j knowledge graph

### Node labels and natural identifiers

`MERGE` is always keyed on these — never on the internal element id.

| Label | Identifier property | Notes |
| --- | --- | --- |
| `Ticker` | `ticker` | uppercase symbol, e.g. `NVDA` |
| `Sector` | `name` | canonical sector name (see seed data) |
| `Politician` | `id` | government identifier, e.g. `P000197` (or slug) |
| `Committee` | `id` | committee acronym/slug, e.g. `HSBA` |
| `Event` | `id` | deterministic: `sha1(type \| tweet_id \| canonical_payload)` |

All nodes additionally carry:
- `tweet_id`, `tweet_url` — traceability to the source tweet
- `first_seen_at` — when Baleen first wrote them

### Relationship types

| Type | From | To | Meaning |
| --- | --- | --- | --- |
| `IN_SECTOR` | `Ticker` | `Sector` | stock belongs to a sector |
| `MEMBER_OF` | `Politician` | `Committee` | politician sits on a committee |
| `OVERSEES` | `Committee` | `Sector` | committee has oversight of a sector |
| `INVOLVES_TICKER` | `Event` | `Ticker` | event references a ticker |
| `INVOLVES_POLITICIAN` | `Event` | `Politician` | congress-trade event references a politician |
| `IN_COMMITTEE` | `Event` | `Committee` | congress-trade event references the committee |

### Full picture for a congress trade

```
(:Event {type:"congress_trade", tweet_id, tweet_url})
    -[:INVOLVES_POLITICIAN]-> (:Politician {id, name, chamber, party})
    -[:MEMBER_OF]-> (:Committee {id, name})
    -[:OVERSEES]-> (:Sector {name})
(:Event)-[:INVOLVES_TICKER]-> (:Ticker {ticker})
    -[:IN_SECTOR]-> (:Sector {name})
```

For an options-flow event only the `INVOLVES_TICKER` arm is written; the ticker
is still linked to its sector via `IN_SECTOR`.

### Read-only query role

The query agent connects with a dedicated read-only Neo4j user. See
[SETUP.md](./SETUP.md#neo4j-read-only-role).

---

## 3. Example Cypher the agent must be able to produce

These are the three MVP example questions (see
[ASSUMPTIONS.md](./ASSUMPTIONS.md#%C2%A754--query-agent--example-questions)).

**Q1 — committee → sectors → tickers**
```cypher
MATCH (c:Committee {id:"HSBA"})-[:OVERSEES]->(s:Sector)<-[:IN_SECTOR]-(t:Ticker)
RETURN s.name AS sector, collect(DISTINCT t.ticker) AS tickers
```

**Q2 — politician's recent trades**
```cypher
MATCH (p:Politician)-[r:INVOLVES_POLITICIAN]-(e:Event)
WHERE p.name = $name
MATCH (e)-[:INVOLVES_TICKER]->(t:Ticker)
RETURN e.transaction_date AS date, t.ticker AS ticker, e.transaction_type AS type
ORDER BY e.transaction_date DESC LIMIT 20
```

**Q3 — options flow in the last 24h by sector**
```cypher
MATCH (e:Event {type:"options_flow"})-[:INVOLVES_TICKER]->(t:Ticker)-[:IN_SECTOR]->(s:Sector)
WHERE e.event_time > datetime() - duration("PT24H")
RETURN s.name AS sector, count(e) AS flow_count, collect(DISTINCT t.ticker) AS tickers
ORDER BY flow_count DESC
```
