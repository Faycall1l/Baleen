"""Alias registry persistence: one canonical spelling per entity."""

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
            row = (
                conn.execute(
                    text(
                        """
                    SELECT canonical_id, canonical_name
                    FROM entity_aliases
                    WHERE entity_type = :entity_type AND alias = :alias
                    """
                    ),
                    {"entity_type": entity_type, "alias": alias},
                )
                .mappings()
                .first()
            )
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
