"""Entity identity persistence, split out of :mod:`app.db.postgres` (K3).

Three registries share one table family: the alias registry that canonicalizes
spelling, the human review queue for ambiguous mentions, and the identity
tombstones (``entity_identities``) that hide merged nodes from the UI.
"""

from __future__ import annotations

from sqlalchemy import text


class AliasRegistryMixin:
    """the alias registry: canonical spelling per entity."""

    def ensure_alias(
        self,
        entity_type: str,
        alias: str,
        canonical_id: str,
        canonical_name: str,
        confidence: float = 1.0,
        source: str = "llm",
    ) -> None:
        """Register (or refresh) one alias for a canonical entity.

        Idempotent per (entity_type, alias). When an alias is re-seen, the
        canonical spelling is *converged*, never duplicated: the stored
        canonical_name is refreshed to the argument, and the confidence is
        decayed toward the newer observation only if it rises. ``source``
        ranks 'seed' > 'review' > 'llm' for tie-break precedence.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO entity_aliases (entity_type, alias, canonical_id, canonical_name, confidence, source)
                    VALUES (:entity_type, :alias, :canonical_id, :canonical_name, :confidence, :source)
                    ON CONFLICT (entity_type, alias) DO UPDATE SET
                        canonical_id   = EXCLUDED.canonical_id,
                        canonical_name = EXCLUDED.canonical_name,
                        confidence     = GREATEST(entity_aliases.confidence, EXCLUDED.confidence),
                        source         = CASE
                                             WHEN entity_aliases.source = 'seed' THEN 'seed'
                                             WHEN EXCLUDED.source = 'seed'     THEN 'seed'
                                             WHEN entity_aliases.source = 'review' THEN 'review'
                                             ELSE EXCLUDED.source
                                         END
                    """
                ),
                {
                    "entity_type": entity_type,
                    "alias": alias,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "confidence": confidence,
                    "source": source,
                },
            )

    def get_canonical(self, entity_type: str, alias: str) -> dict | None:
        """The canonical (id, name) a visited alias already resolves to."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT canonical_id, canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND alias = :alias
                    """
                ),
                {"entity_type": entity_type, "alias": alias},
            ).mappings().first()
            return dict(row) if row is not None else None

    def get_canonical_name(self, entity_type: str, canonical_id: str) -> str | None:
        """The canonical spelling registered for a stable id, if any."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND canonical_id = :canonical_id
                    ORDER BY source = 'seed' DESC, confidence DESC, id ASC
                    LIMIT 1
                    """
                ),
                {"entity_type": entity_type, "canonical_id": canonical_id},
            ).scalar_one_or_none()
            return row

    def list_aliases(self, entity_type: str, canonical_id: str) -> list[dict]:
        """Every registered spelling for a canonical entity (for the UI / audit)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT alias, canonical_name, confidence, source, created_at
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND canonical_id = :canonical_id
                    ORDER BY confidence DESC, id ASC
                    """
                ),
                {"entity_type": entity_type, "canonical_id": canonical_id},
            ).mappings()
            return [dict(row) for row in rows]

    def list_aliases_by_type(self, entity_type: str) -> list[dict]:
        """All registered aliases for an entity type (drives the fuzzy pass)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT alias, canonical_id, canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type
                    ORDER BY canonical_id, id
                    """
                ),
                {"entity_type": entity_type},
            ).mappings()
            return [dict(row) for row in rows]


class ResolutionQueueMixin:
    """the human review queue for ambiguous entity mentions."""

    def insert_resolution_suggestion(
        self,
        entity_type: str,
        candidate_id: str,
        candidate_name: str,
        canonical_id: str,
        canonical_name: str,
        confidence: float,
        reason: str,
    ) -> int | None:
        """Queue a near-duplicate pair for human review. Returns its id, or
        ``None`` if the same (entity_type, candidate_id, canonical_id) pair is
        already queued (any status)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    INSERT INTO entity_resolution_queue
                        (entity_type, candidate_id, candidate_name, canonical_id,
                         canonical_name, confidence, reason)
                    VALUES (:entity_type, :candidate_id, :candidate_name, :canonical_id,
                            :canonical_name, :confidence, :reason)
                    ON CONFLICT (entity_type, candidate_id, canonical_id) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "entity_type": entity_type,
                    "candidate_id": candidate_id,
                    "candidate_name": candidate_name,
                    "canonical_id": canonical_id,
                    "canonical_name": canonical_name,
                    "confidence": confidence,
                    "reason": reason,
                },
            )
            return result.scalar_one_or_none()

    def pending_resolutions(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, candidate_name,
                           canonical_id, canonical_name, confidence, reason, created_at
                    FROM entity_resolution_queue
                    WHERE status = 'pending'
                    ORDER BY confidence DESC, created_at ASC
                    """
                )
            ).mappings()
            return [dict(row) for row in rows]

    def mark_resolution(self, queue_id: int, status: str) -> bool:
        """Flip a queue entry to 'confirmed' or 'rejected'. Returns False when
        the id does not exist (idempotent caller behaviour)."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    """
                    UPDATE entity_resolution_queue
                    SET status = :status, resolved_at = now()
                    WHERE id = :id AND status = 'pending'
                    """
                ),
                {"status": status, "id": queue_id},
            )
            return result.rowcount == 1

    def confirm_resolution(self, queue_id: int) -> bool:
        """Confirm a queue entry AND record its identity remap, atomically.

        The human's decision is two things at once: (a) this pair is resolved,
        and (b) from now on ``candidate_id`` is an alias of ``canonical_id`` at
        the id level. Both need to land or neither does, so they share one
        transaction. Returns False when the entry was not pending (already
        resolved or unknown — idempotent).
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    UPDATE entity_resolution_queue
                    SET status = 'confirmed', resolved_at = now()
                    WHERE id = :id AND status = 'pending'
                    RETURNING entity_type, candidate_id, canonical_id, confidence
                    """
                ),
                {"id": queue_id},
            ).mappings().first()
            if row is None:
                return False
            conn.execute(
                text(
                    """
                    INSERT INTO entity_identities
                        (entity_type, candidate_id, canonical_id, confidence, source, resolution_id)
                    VALUES (:entity_type, :candidate_id, :canonical_id, :confidence, 'review', :resolution_id)
                    ON CONFLICT (entity_type, candidate_id) DO UPDATE SET
                        canonical_id = EXCLUDED.canonical_id,
                        confidence   = GREATEST(entity_identities.confidence, EXCLUDED.confidence),
                        resolution_id = EXCLUDED.resolution_id
                    """
                ),
                {
                    "entity_type": row["entity_type"],
                    "candidate_id": row["candidate_id"],
                    "canonical_id": row["canonical_id"],
                    "confidence": row["confidence"],
                    "resolution_id": queue_id,
                },
            )
            return True

    def get_resolution(self, queue_id: int) -> dict | None:
        """A queue entry by id, regardless of status (for the apply/undo flows)."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, candidate_name,
                           canonical_id, canonical_name, confidence, reason, status, created_at
                    FROM entity_resolution_queue
                    WHERE id = :id
                    """
                ),
                {"id": queue_id},
            ).mappings().first()
            return dict(row) if row is not None else None


class IdentityTombstoneMixin:
    """identity tombstones (``merged_into``) and remap history."""

    def get_identity(self, entity_type: str, candidate_id: str) -> dict | None:
        """The canonical an id has been remapped to, if any."""
        with self.engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT candidate_id, canonical_id, confidence
                    FROM entity_identities
                    WHERE entity_type = :entity_type AND candidate_id = :candidate_id
                    """
                ),
                {"entity_type": entity_type, "candidate_id": candidate_id},
            ).mappings().first()
            return dict(row) if row is not None else None

    def record_identity(
        self,
        entity_type: str,
        candidate_id: str,
        canonical_id: str,
        confidence: float = 1.0,
        source: str = "review",
        resolution_id: int | None = None,
    ) -> None:
        """Declare ``candidate_id`` an alias of ``canonical_id`` at the id level.

        Idempotent per (entity_type, candidate_id); a later declaration simply
        retargets the candidate. No graph rewrite happens here — that is the
        caller's separate, explicit step.
        """
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO entity_identities
                        (entity_type, candidate_id, canonical_id, confidence, source, resolution_id)
                    VALUES (:entity_type, :candidate_id, :canonical_id, :confidence, :source, :resolution_id)
                    ON CONFLICT (entity_type, candidate_id) DO UPDATE SET
                        canonical_id   = EXCLUDED.canonical_id,
                        confidence     = GREATEST(entity_identities.confidence, EXCLUDED.confidence),
                        resolution_id  = EXCLUDED.resolution_id,
                        source         = EXCLUDED.source
                    """
                ),
                {
                    "entity_type": entity_type,
                    "candidate_id": candidate_id,
                    "canonical_id": canonical_id,
                    "confidence": confidence,
                    "source": source,
                    "resolution_id": resolution_id,
                },
            )

    def list_identity_remaps(self, entity_type: str | None = None) -> list[dict]:
        """Every remap row (candidate -> canonical), optionally per type."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT id, entity_type, candidate_id, canonical_id, confidence,
                           source, created_at, resolution_id
                    FROM entity_identities
                    WHERE (CAST(:entity_type AS TEXT) IS NULL OR entity_type = :entity_type)
                    ORDER BY entity_type, candidate_id
                    """
                ),
                {"entity_type": entity_type},
            ).mappings()
            return [dict(row) for row in rows]
