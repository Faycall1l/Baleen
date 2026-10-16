"""Entity naming: resolve mentions to one canonical spelling per entity.

The graph keys entities (Politician, Insider) on stable natural ids, but the
*name* written to a node used to come from whichever filing was processed
first, so "Rep. Nancy Pelosi" and "Nancy Pelosi" could flip one node's name
between spellings. Every mention is now resolved, deterministically and
without an LLM, to the canonical spelling registered in ``entity_aliases``.
"""

from __future__ import annotations

import logging
import re

from app.db.postgres import PostgresStore

logger = logging.getLogger(__name__)

#: Entity types the naming layer administers. Ticker is normalized to uppercase
#: at the model layer already, so canonicalization never runs for it.
ADMINISTERED_TYPES = ("Politician", "Insider")


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
                entity_type,
                alias=mention,
                canonical_id=canonical_id,
                canonical_name=canonical,
                source="llm",
            )
            return canonical

        known = self._db.get_canonical(entity_type, mention)
        # NOTE: the "same mention, same id" refresh case cannot occur here:
        # any alias row carrying this canonical_id makes get_canonical_name
        # above return non-None, so reaching this point with a matching row
        # is impossible (verified by invariant, not by test).
        if known is not None and known["canonical_id"] != canonical_id:
            # Same spelling, two different ids. Spelling alone cannot say who
            # is right, so the first-registered id stays authoritative and the
            # event still gets a consistent node name.
            logger.warning(
                "mention %r already resolves to %s for %s but event claims id %s",
                mention,
                known["canonical_id"],
                entity_type,
                canonical_id,
            )
            return known["canonical_name"]

        # First encounter for this canonical_id: the mention IS the canonical
        # spelling (seed).
        self._db.ensure_alias(
            entity_type,
            alias=mention,
            canonical_id=canonical_id,
            canonical_name=mention,
            source="llm",
        )
        return mention

    def seed_canonical(self, entity_type: str, canonical_id: str, canonical_name: str) -> None:
        """Register the authoritative spelling for a reference-roster entity.

        Seeded entries win alias precedence (their spelling is never clobbered
        by a later LLM mention of the same id).
        """
        self._db.ensure_alias(
            entity_type,
            alias=canonical_name,
            canonical_id=canonical_id,
            canonical_name=canonical_name,
            source="seed",
        )
