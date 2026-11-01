# Research context

Baleen is an engineering artifact for **monitoring public information streams**,
not a trading product. It was built to answer a question that media-monitoring
research keeps running into: *how much of a monitoring pipeline can be made
traceable, reproducible and auditable when the underlying stream is noisy,
multi-source and partly unstructured?*

## What the system demonstrates

| Monitoring concern | How Baleen handles it |
| --- | --- |
| **Multi-source ingestion** | Eight independent keyless sources (House Clerk, Senate mirror, SEC EDGAR, CBOE, and four RSS/Atom news outlets) plus optional X. Each source fails in isolation and backs off instead of retrying hot. |
| **Event extraction** | Structured filings are parsed deterministically — identifiers, dates and amounts come from the record, never from a model. Free text goes through schema-constrained LLM extraction; low-confidence output is quarantined as `UnparsedEvent` rather than force-fit. |
| **Entity resolution** | Filers, tickers and committees resolve to canonical nodes through a reference roster and an alias registry. Unknown entities get stable slugs instead of guesses. |
| **Relationship modelling** | A Neo4j graph links people, committees, sectors, tickers and events, so oversight relationships and co-occurrence are queryable rather than buried in rows. |
| **Natural-language access** | Questions become Cypher, are validated against a label/relationship allowlist, and run on a dedicated read-only role when one is configured. The query is returned alongside the answer. "No matching data" is a first-class answer. |
| **Alerting** | Three deterministic rules (oversight-sector trades, large insider buys, unusual options volume) evaluated per event, with mute and read state. |
| **Reporting** | A brief per event and a rolling daily digest, grounded in the event's graph neighbourhood and carrying source links. |
| **Auditability** | Every node and edge keeps a source URL; every generated answer shows the Cypher it ran; ingestion is idempotent on stable ids. |

## Why this is relevant to media monitoring

The hard part of monitoring is not fetching. It is deciding what counts as an
event, keeping the chain back to the primary record intact, and not letting a
language model invent the parts that matter. Baleen is a working testbed for
those decisions on a stream where ground truth is public and checkable:
congressional disclosures and SEC filings are published, dated and citable.

The same architecture transfers to news and social streams: source adapters →
normalized records → deterministic-first extraction → entity graph → rules and
reports → grounded query. The graph is the part that makes relationships
visible; the deterministic-first extraction is the part that makes the output
defensible.

## Evaluation status

- **Test suite:** 435 backend tests and 85 frontend tests, run in CI against
  Postgres and Neo4j service containers.
- **Live verification:** the keyless feeds have been run end to end against the
  real endpoints; a scheduler tick ingests, extracts, writes the graph,
  evaluates alerts and publishes reports without manual intervention.
- **Known limits:** there is no formal benchmark for text-to-Cypher accuracy
  yet; the reference roster covers a hand-curated subset of members, so
  historical trades for unlisted filers sit under slug nodes; options data is
  delayed, not real time; and free-text extraction quality depends on the
  configured model.

Those limits are documented rather than hidden. A research use should treat the
system as a reproducible pipeline and a source of hypotheses, not as a
complete or authoritative dataset.

## Reproducing a monitoring run

See [DEMO.md](DEMO.md) for a five-minute walkthrough and
[SETUP.md](SETUP.md) for configuration. The keyless path needs no API keys:

```bash
docker compose up --build -d
docker compose exec backend python -m scripts.seed_reference
docker compose exec backend python -m scripts.ingest_once --real
```
