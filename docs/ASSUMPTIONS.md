# Design Assumptions

> **Context.** The full `Baleen_Project_Design.md` spec was not available when this
> implementation was started (the file was referenced but not attached). Per the
> build prompt, development proceeded using the details embedded in the prompt.
> This document records every design decision made in place of the missing spec
> sections, so the exact sections of the spec (`§5.2`, `§5.3`, `§5.4`, `§5.5`,
> `§8`, `§9`) can be reconciled later if the original file surfaces.

If you have the original spec, diff this document against it and update the code
accordingly. Everything else (phases, guardrails, stack) was followed as written.

---

## §5.2 — Event schemas (Pydantic)

Event schemas live in `backend/app/models/events.py`. The base model carries
tweet provenance (`tweet_id`, `tweet_url`) and `confidence`, so every node/edge
written to the graph is traceable. A `type` discriminator field selects the
concrete event:

| Event | Type string | Key fields |
| --- | --- | --- |
| `OptionsFlowEvent` | `options_flow` | `ticker`, `contract_type` (call/put), `strike`, `expiration`, `premium`, `volume`, `open_interest`, `sentiment` (bullish/bearish/neutral), `event_time` |
| `CongressTradeEvent` | `congress_trade` | `politician_name`, `chamber` (house/senate), `party`, `ticker`, `transaction_type` (buy/sell), `amount_range`, `transaction_date` |
| `InsiderTradeEvent` | `insider_trade` | `insider_name`, `insider_cik`, `role`, `ticker`, `transaction_type` (buy/sell), `shares`, `price`, `amount_range`, `transaction_date` (SEC Form 4, open-market P/S only) |
| `UnparsedEvent` | `unparsed` | `raw_text`, `reason` (why extraction failed / low confidence) |

`DarkPoolEvent` is explicitly out of MVP scope and omitted.

## §5.3 — Graph schema (Neo4j)

Documented fully in [SCHEMA.md](./SCHEMA.md). Node labels: `Ticker`, `Sector`,
`Politician`, `Committee`, `Event`. Relationship types:

| Relationship | From | To | Meaning |
| --- | --- | --- | --- |
| `IN_SECTOR` | `Ticker` | `Sector` | a stock belongs to a sector |
| `MEMBER_OF` | `Politician` | `Committee` | politician sits on a committee |
| `OVERSEES` | `Committee` | `Sector` | committee has oversight of a sector |
| `INVOLVES_TICKER` | `Event` | `Ticker` | an event references a ticker |
| `INVOLVES_POLITICIAN` | `Event` | `Politician` | a congress-trade event references a politician |
| `IN_COMMITTEE` | `Event` | `Committee` | a congress-trade event references the committee |

Natural identifiers used for `MERGE`: `Ticker.ticker`, `Sector.name`,
`Politician.id` (government identifier), `Committee.id`, `Event.id`
(deterministic hash of type + tweet_id + payload).

## §5.4 — Query agent & example questions

The query agent uses a fixed, documented schema as LLM context (never inferred)
and a read-only Neo4j role. Generated Cypher is validated against an allowlist
before execution (see [docs/ARCHITECTURE.md#query-agent](./ARCHITECTURE.md)).

The three example questions the MVP must answer:

1. **"What sectors does the House Financial Services Committee oversee, and
   which of those sectors have tickers in the graph?"**
   → `(Committee)-[:OVERSEES]->(Sector)<-[:IN_SECTOR]-(Ticker)`
2. **"Which tickers has [Politician] traded recently, and when?"**
   → `(Politician)<-[:INVOLVES_POLITICIAN]-(Event)-[:INVOLVES_TICKER]->(Ticker)`
3. **"Show unusual options flow in the last 24 hours by sector."**
   → `(Event {type:"options_flow"})-[:INVOLVES_TICKER]->(Ticker)-[:IN_SECTOR]->(Sector)`

## §5.5 — Alert engine

Exactly one MVP rule: `politician_trades_in_oversight_sector`. It fires when a
`CongressTradeEvent` references a politician whose committees oversee the sector
the traded ticker belongs to:

```
(Event)-[:INVOLVES_POLITICIAN]->(Politician)-[:MEMBER_OF]->(Committee)-[:OVERSEES]->(Sector)
(Event)-[:INVOLVES_TICKER]->(Ticker)-[:IN_SECTOR]->(Sector)     # same Sector
```

On trigger, the LLM drafts a 2–3 sentence factual alert with source links,
stored in the `alerts` table. Full documentation in [ALERTS.md](./ALERTS.md).

## §8 — MVP scope

In scope: ingestion (X API v2 recent-search), structured extraction of the three
event types, graph writing, query agent, one alert rule, three UI surfaces
(Graph, Query Bar, Alerts Panel), docker-compose deployment. Out of scope:
`DarkPoolEvent`, extra alert rules, dashboards, extra UI pages, Kafka, scraping
that bypasses the X API.

## §9 — Repository structure

Approximated from the phase description, documented in [ARCHITECTURE.md](./ARCHITECTURE.md).
The layout is `backend/` (FastAPI app + tests) and `frontend/` (React + Vite),
with `docs/` for documentation and Docker Compose at the root.
