"""Entity resolution — canonical-name layer (v1).

The graph stores entities (Politician, Insider, Ticker) keyed on stable natural
ids (``politician_id``, ``insider_id``, the ticker symbol). The *name* written
to a node, however, used to come straight from whichever tweet was processed
first, so "Rep. Nancy Pelosi" and "Nancy Pelosi" could produce the same node's
``name`` drifting between spellings.

This module implements the **naming layer** of entity resolution (separated
from identity, per current practice): every mention is resolved, determinis-
tically and without LLM involvement, to a single canonical spelling registered
in the ``entity_aliases`` table:

- ``Politician`` and ``Insider`` names are canonicalized on write.
- A human-facing review queue (``entity_resolution_queue``) collects
  near-duplicate candidates from a fuzzy ``suggest_merges`` pass (blocking by
  first characters + ``difflib`` ratio, thresholds from the published ER
  literature: auto-band at 0.95+ vs. gray band 0.85-0.95) for confirmation or
  rejection.

The **identity layer** (embedding-based cross-id detection, Neo4j node merges,
edge rewriting) is intentionally out of scope for v1; ``approve`` currently
only updates resolution state, it does not rewrite graph edges.
"""

from __future__ import annotations

import difflib
import logging
import re

from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)

#: Entity types the naming layer administers. Ticker is normalized to uppercase
#: at the model layer already, so canonicalization never runs for it.
ADMINISTERED_TYPES = ("Politician", "Insider")

#: Fuzzy pass thresholds (from the entity-resolution literature): pairs scoring
#: at or above ``AUTO_MERGE_THRESHOLD`` are unambiguous; the band below it down
#: to ``REVIEW_MIN_THRESHOLD`` requires a human decision. Pairs under the min
#: are unrelated.
AUTO_MERGE_THRESHOLD = 0.95
REVIEW_MIN_THRESHOLD = 0.85


def normalize_mention(mention: str) -> str:
    """Fold a raw mention to a canonical key used for alias lookups.

    Lowercases, strips punctuation (apostrophes/hyphens folded to spaces) and
    collapses whitespace. Order is preserved: "Nancy Pelosi" and "Pelosi
    Nancy" deliberately differ, because reversing token order is a different
    name, not a variant of the same one.
    """
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", mention.lower())).strip()


class EntityResolver:
    """Resolve entity mentions to canonical names via the alias registry.

    Thread-safety: each call is a short transaction against Postgres; the
    resolver itself holds no mutable state, so concurrent pipeline workers can
    share one instance.
    """

    def __init__(self, db: PostgresStore) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Canonical-name resolution
    # ------------------------------------------------------------------

    def canonical_name(self, entity_type: str, mention: str, canonical_id: str) -> str:
        """Return the canonical spelling to write for ``(mention, canonical_id)``.

        Resolution is deterministic and never asks the LLM:

        1. If ``canonical_id`` already has a registered canonical spelling,
           use it and register this mention as a new alias (first-mention
           convergence — the id is authoritative, the spelling is not).
        2. Otherwise, if this exact mention already maps to a canonical entity
           (an alias registered from a different tweet), use that canonical's
           name — but only when it belongs to the same ``canonical_id``; a
           spelling conflict across ids is data worth surfacing, so it is logged,
           queued for human review, and the *first-registered* canonical wins.
        3. First encounter: register the mention itself as the seed alias.
        """
        canonical = self._db.get_canonical_name(entity_type, canonical_id)
        if canonical is not None:
            self._db.ensure_alias(
                entity_type, alias=mention, canonical_id=canonical_id,
                canonical_name=canonical, source="llm",
            )
            return canonical

        known = self._db.get_canonical(entity_type, mention)
        if known is not None and known["canonical_id"] == canonical_id:
            # Alias already points at this exact id; refresh the spelling.
            self._db.ensure_alias(
                entity_type, alias=mention, canonical_id=canonical_id,
                canonical_name=known["canonical_name"], source="llm",
            )
            return known["canonical_name"]
        if known is not None and known["canonical_id"] != canonical_id:
            # Same spelling, two different ids. We cannot tell who is right from
            # spelling alone, so the *first-registered* id stays authoritative,
            # the event still gets a consistent node name, and the clash is
            # queued for a human rather than silently clobbered. Idempotent:
            # the queue unique-pair constraint prevents re-insertion.
            logger.warning(
                "mention %r already resolves to %s for %s but event claims id %s; "
                "queuing alias clash for review",
                mention, known["canonical_id"], entity_type, canonical_id,
            )
            self._db.insert_resolution_suggestion(
                entity_type,
                candidate_id=canonical_id,
                candidate_name=mention,
                canonical_id=known["canonical_id"],
                canonical_name=known["canonical_name"],
                confidence=1.0,
                reason="alias_clash",
            )
            return known["canonical_name"]

        # First encounter for this canonical_id: the mention IS the canonical
        # spelling (seed). Identity disputes are caught by the fuzzy pass.
        self._db.ensure_alias(
            entity_type, alias=mention, canonical_id=canonical_id,
            canonical_name=mention, source="llm",
        )
        return mention

    def seed_canonical(self, entity_type: str, canonical_id: str, canonical_name: str) -> None:
        """Register the authoritative spelling for a reference-roster entity.

        Seeded entries win alias precedence (their spelling is never clobbered
        by a later LLM mention of the same id).
        """
        self._db.ensure_alias(
            entity_type, alias=canonical_name, canonical_id=canonical_id,
            canonical_name=canonical_name, source="seed",
        )

    # ------------------------------------------------------------------
    # Fuzzy near-duplicate pass (feeds the human review queue)
    # ------------------------------------------------------------------

    def suggest_merges(self, entity_type: str) -> int:
        """Scan the alias registry for near-duplicates and queue the gray band.

        Blocking: aliases are bucketed by the first two lowercase characters of
        their normalized mention, turning an all-pairs comparison into several
        small in-block comparisons. Within a block, pairs are scored with
        ``difflib.SequenceMatcher`` (stdlib, deterministic). Every distinct
        pair of canonical entities whose names score in the gray band
        [0.85, 0.95) — or equal or above 0.95 across *different* ids — is queued
        for human review. Idempotent: re-running never duplicates a pair.

        Returns the number of new suggestions added.
        """
        aliases = self._db.list_aliases_by_type(entity_type)
        if not aliases:
            return 0

        blocks: dict[str, list[dict]] = {}
        for a in aliases:
            key = normalize_mention(a["alias"])[:2] or "?"
            blocks.setdefault(key, []).append(a)

        added = 0
        for bucket in blocks.values():
            added += self._score_bucket(entity_type, bucket)
        logger.info(
            "resolution suggestions for %s: %d new queued across %d blocks",
            entity_type, added, len(blocks),
        )
        return added

    def approve(self, queue_id: int) -> bool:
        """Confirm a queued pair. v1 only marks resolution state; the graph
        (identity-layer) merge is a follow-up."""
        return self._db.mark_resolution(queue_id, "confirmed")

    def reject(self, queue_id: int) -> bool:
        return self._db.mark_resolution(queue_id, "rejected")

    def pending_reviews(self) -> list[dict]:
        return self._db.pending_resolutions()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _score_bucket(self, entity_type: str, bucket: list[dict]) -> int:
        """Score all name pairs in one block against the thresholds.

        A pair is only meaningful across *different* canonical ids; aliases
        within the same id are spelling variants of one target and are folded
        by the naming layer, never queued. Each id contributes its registered
        canonical spelling as the representative name (seeded entries win).
        """
        by_id: dict[str, str] = {}
        for a in bucket:
            by_id[a["canonical_id"]] = a["canonical_name"]

        pairs = []
        ids = sorted(by_id)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                id_a, id_b = ids[i], ids[j]
                if id_a == id_b:
                    continue
                name_a = normalize_mention(by_id[id_a])
                name_b = normalize_mention(by_id[id_b])
                ratio = difflib.SequenceMatcher(None, name_a, name_b).ratio()
                if ratio < REVIEW_MIN_THRESHOLD:
                    continue
                if ratio >= AUTO_MERGE_THRESHOLD:
                    # Highly similar spelling but different ids: dangerous
                    # ambiguous alias — always a human call.
                    pairs.append((id_a, name_a, id_b, name_b, round(min(ratio, 0.999), 3)))
                else:
                    pairs.append((id_a, name_a, id_b, name_b, round(ratio, 3)))

        added = 0
        for id_a, name_a, id_b, name_b, ratio in pairs:
            inserted = self._db.insert_resolution_suggestion(
                entity_type,
                candidate_id=id_a,
                candidate_name=name_a,
                canonical_id=id_b,
                canonical_name=name_b,
                confidence=ratio,
                reason="fuzzy_match",
            )
            if inserted is not None:
                added += 1
        return added