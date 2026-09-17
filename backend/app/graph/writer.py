"""Idempotent Neo4j graph writer (spec §5.3).

Every write is a Cypher ``MERGE`` keyed on a natural identifier (see
``app.graph.schema.LABEL_KEYS``) — never ``CREATE``. Re-writing the same event
is a no-op. All nodes are stamped with the source tweet id/url for traceability.
"""

from __future__ import annotations

import logging
from typing import Callable

from app.db.neo4j import Neo4jStore
from app.graph.resolution import EntityResolver
from app.graph.schema import LABEL_KEYS
from app.graph.seed_data import COMMITTEES, TICKER_SECTORS
from app.models.events import CongressTradeEvent, Event, InsiderTradeEvent, OptionsFlowEvent, UnparsedEvent

logger = logging.getLogger(__name__)

#: Resolves a ticker symbol to its sector name; ``None`` when unknown.
SectorResolver = Callable[[str], str | None]

DEFAULT_RESOLVER: SectorResolver = lambda ticker: TICKER_SECTORS.get(ticker)


class GraphWriter:
    def __init__(
        self,
        store: Neo4jStore,
        sector_resolver: SectorResolver | None = None,
        entity_resolver: EntityResolver | None = None,
    ) -> None:
        self.store = store
        self.sector_resolver = sector_resolver or DEFAULT_RESOLVER
        # When set, Politician/Insider nodes are written with their canonical
        # spelling (entity resolution, naming layer) instead of whatever mention
        # the LLM produced. None keeps the historical raw-mention behaviour.
        self.entity_resolver = entity_resolver

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write_event(self, event: Event) -> str:
        """Upsert one event into the graph. Returns its deterministic id.

        All merges for the event run inside a single server transaction
        (#19): the fan-out (nodes, sector resolution, N committees) is one
        round-trip instead of ~8 + 3N sequential ones, and the event becomes
        visible atomically.
        """
        self._stmts: list[tuple[str, dict]] = []
        self._merge_event(event)
        if isinstance(event, (OptionsFlowEvent, CongressTradeEvent, InsiderTradeEvent)):
            self._write_ticker_flow(event)
        if isinstance(event, CongressTradeEvent):
            self._write_politician_flow(event)
        if isinstance(event, InsiderTradeEvent):
            self._write_insider_flow(event)
        with self.store.transaction() as run:
            for query, params in self._stmts:
                run(query, **params)
        logger.info("event %s written to graph (type=%s)", event.deterministic_id, event.event_type)
        return event.deterministic_id

    # ------------------------------------------------------------------
    # Node / edge merge helpers
    # ------------------------------------------------------------------

    def _record(self, query: str, params: dict) -> None:
        self._stmts.append((query, params))

    def _merge_node(self, label: str, props: dict, provenance: dict) -> None:
        key = LABEL_KEYS[label]
        self._record(
            f"""
            MERGE (n:{label} {{{key}: $key_value}})
            SET n += $props
            SET n.first_seen_at = COALESCE(n.first_seen_at, datetime()),
                n.tweet_id = COALESCE($tweet_id, n.tweet_id),
                n.tweet_url = COALESCE($tweet_url, n.tweet_url)
            """,
            {
                "key_value": props[key],
                "props": {k: v for k, v in props.items() if k != key},
                "tweet_id": provenance.get("tweet_id"),
                "tweet_url": provenance.get("tweet_url"),
            },
        )

    def _merge_relation(
        self, a_label: str, a_key_value, rel: str, b_label: str, b_key_value, provenance: dict
    ) -> None:
        a_key, b_key = LABEL_KEYS[a_label], LABEL_KEYS[b_label]
        self._record(
            # Two MATCH clauses, not one comma-joined pattern: the comma form
            # plans a cartesian product between the disconnected patterns
            # (harmless here — single rows — but noisy in server notifications
            # and slower at scale).
            f"""
            MATCH (a:{a_label} {{{a_key}: $a_value}})
            MATCH (b:{b_label} {{{b_key}: $b_value}})
            MERGE (a)-[r:{rel}]->(b)
            SET r.tweet_id = COALESCE($tweet_id, r.tweet_id),
                r.tweet_url = COALESCE($tweet_url, r.tweet_url)
            """,
            {
                "a_value": a_key_value,
                "b_value": b_key_value,
                "tweet_id": provenance.get("tweet_id"),
                "tweet_url": provenance.get("tweet_url"),
            },
        )

    def _provenance(self, event: Event) -> dict:
        return {"tweet_id": event.tweet_id, "tweet_url": event.tweet_url}

    # ------------------------------------------------------------------
    # Event-specific flows
    # ------------------------------------------------------------------

    def _merge_event(self, event: Event) -> None:
        # mode="python" keeps datetimes/dates as native temporal values so
        # event_time/transaction_date are queryable as temporal types (e.g. the
        # documented `datetime() - duration()` window filters), not ISO strings.
        props = event.model_dump(mode="python", exclude={"tweet_id", "tweet_url"})
        # The schema exposes the event kind as `type` on the Event node.
        props["type"] = props.pop("event_type")
        props["id"] = event.deterministic_id
        self._merge_node("Event", props, self._provenance(event))

    def _merge_ticker(self, ticker: str, tweet_id: str, tweet_url: str) -> str | None:
        self._merge_node(
            "Ticker",
            {"ticker": ticker},
            {"tweet_id": tweet_id, "tweet_url": tweet_url},
        )
        sector = self.sector_resolver(ticker)
        if sector:
            self._merge_sector(sector, tweet_id, tweet_url)
            self._merge_relation(
                "Ticker", ticker, "IN_SECTOR", "Sector", sector,
                {"tweet_id": tweet_id, "tweet_url": tweet_url},
            )
        return sector

    def _merge_sector(self, name: str, tweet_id: str, tweet_url: str) -> None:
        self._merge_node(
            "Sector",
            {"name": name},
            {"tweet_id": tweet_id, "tweet_url": tweet_url},
        )

    def _write_ticker_flow(self, event: OptionsFlowEvent | CongressTradeEvent | InsiderTradeEvent) -> None:
        self._merge_ticker(event.ticker, event.tweet_id, event.tweet_url)
        self._merge_relation(
            "Event", event.deterministic_id, "INVOLVES_TICKER", "Ticker", event.ticker,
            self._provenance(event),
        )

    def _write_politician_flow(self, event: CongressTradeEvent) -> None:
        provenance = self._provenance(event)
        name = event.politician_name
        identity_id = event.politician_id
        if self.entity_resolver is not None:
            # A confirmed identity remap collapses the id onto its canonical
            # BEFORE any write: mentions of a tombstoned candidate never
            # resurrect a separate node, and INVOLVES_*/MEMBER_OF attach to the
            # canonical node the engine merged into.
            identity_id = self.entity_resolver.canonical_identity("Politician", event.politician_id)
            name = self.entity_resolver.canonical_name("Politician", event.politician_name, identity_id)
        self._merge_node(
            "Politician",
            {
                "id": identity_id,
                "name": name,
                "chamber": event.chamber,
                **({"party": event.party} if event.party else {}),
            },
            provenance,
        )
        self._merge_relation(
            "Event", event.deterministic_id, "INVOLVES_POLITICIAN", "Politician",
            identity_id, provenance,
        )
        for committee_id in event.committee_ids:
            self._merge_node(
                "Committee",
                {
                    "id": committee_id,
                    **({"name": COMMITTEES[committee_id]} if committee_id in COMMITTEES else {}),
                },
                provenance,
            )
            self._merge_relation(
                "Event", event.deterministic_id, "IN_COMMITTEE", "Committee", committee_id,
                provenance,
            )
            self._merge_relation(
                "Politician", identity_id, "MEMBER_OF", "Committee", committee_id,
                provenance,
            )

    def _write_insider_flow(self, event: InsiderTradeEvent) -> None:
        provenance = self._provenance(event)
        name = event.insider_name
        identity_id = event.insider_id
        if self.entity_resolver is not None:
            identity_id = self.entity_resolver.canonical_identity("Insider", event.insider_id)
            name = self.entity_resolver.canonical_name("Insider", event.insider_name, identity_id)
        self._merge_node(
            "Insider",
            {
                "id": identity_id,
                "name": name,
                **({"cik": event.insider_cik} if event.insider_cik else {}),
                **({"role": event.role} if event.role else {}),
            },
            provenance,
        )
        self._merge_relation(
            "Event", event.deterministic_id, "INVOLVES_INSIDER", "Insider",
            identity_id, provenance,
        )
