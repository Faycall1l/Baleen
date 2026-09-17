"""Identity dedup layer: apply confirmed identities to the graph.

The naming layer (``app/graph/resolution.py``) decides — with human review in
the gray band — that two ids are the same person. This engine *rewrites the
graph* to reflect that decision:

* relationships of the candidate node are transferred to the canonical node
  (duplicates already present on the canonical are skipped);
* the candidate node is tombstoned with ``merged_into = canonical id`` so a
  future write cannot silently resurrect a separate node for the same id;
* a directed ``SAME_AS`` edge (candidate -> canonical) records the merge with
  source / confidence / resolution id / timestamp for full provenance.

The whole rewrite runs atomically in one Neo4j transaction. It is idempotent:
re-applying the same confirmed resolution on an already-tombstoned candidate
is a no-op.

Relationship types are read from the graph itself, so the engine stays
agnostic to the schema's vocabulary; each type is validated against a strict
identifier pattern before it is interpolated into a Cypher statement.
"""

import re

from app.config import get_settings
from app.db.neo4j import Neo4jStore
from app.graph.resolution import EntityResolver

_RELTYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Identity node labels the engine knows how to merge. Anything else is refused:
# this is a deliberate, auditable surface, not a generic delete-everything tool.
_MERGEABLE_LABELS = {"Politician", "Insider"}


class IdentityEngine:
    def __init__(self, resolver: EntityResolver, neo4j: Neo4jStore,
                 merge_timeout: int | None = None):
        self.resolver = resolver
        self.neo4j = neo4j
        # The whole rewrite needs one cumulative budget distinct from the
        # per-statement cap (#23): finite so a stalled Neo4j can't hold locks
        # indefinitely, and failure is safe because the merge is atomic and
        # re-apply resumes an incomplete tombstone (#22).
        self.merge_timeout = merge_timeout or get_settings().neo4j_merge_timeout_seconds

    def apply_merge(self, resolution_id: int, label: str) -> dict:
        """Rewrite the graph for a *confirmed* resolution.

        Returns a summary dict::

            {"transferred": int, "skipped_duplicates": int,
             "same_as": 1, "already_merged": bool}

        Raises ``ValueError`` if the resolution is unknown / not confirmed or
        the label is not mergeable, and ``RuntimeError`` if the candidate is
        already tombstoned into a *different* canonical.
        """
        resolution = self.resolver.get_resolution(resolution_id)
        if resolution is None or resolution["status"] != "confirmed":
            raise ValueError(f"resolution {resolution_id} is not a confirmed merge")
        if label not in _MERGEABLE_LABELS:
            raise ValueError(f"refusing to merge label {label!r}")
        candidate_id = resolution["candidate_id"]
        canonical_id = resolution["canonical_id"]
        confidence = resolution["confidence"]
        # Merges must not be aborted mid-flight by the app-wide statement cap
        # (#23): the whole rewrite is one transaction, so an abort commits
        # nothing and no partial transfer can corrupt the graph. Merges are
        # human-gated and small; they run inside one atomic transaction with
        # their own cumulative budget (neo4j_merge_timeout_seconds). Should a
        # *pre-existing* tombstone ever be missing its SAME_AS edge (manual
        # ops, older versions), the merge resumes and heals it instead of
        # reporting "already merged" (see _merge_tx).
        return self.neo4j.write_transaction(
            lambda run: self._merge_tx(
                run, label, candidate_id, canonical_id, confidence, resolution_id
            ),
            timeout=self.merge_timeout,
        )

    def _merge_tx(self, run, label, candidate_id, canonical_id, confidence, resolution_id) -> dict:
        anchor = run(
            f"MATCH (cand:{label} {{id:$cid}}), (canon:{label} {{id:$xid}}) "
            "OPTIONAL MATCH (cand)-[sa:SAME_AS]->(canon) "
            "RETURN elementId(cand) AS c, cand.merged_into AS merged_into, "
            "count(sa) AS same_as",
            cid=candidate_id,
            xid=canonical_id,
        )
        if not anchor:
            raise ValueError(
                f"candidate {candidate_id!r} or canonical {canonical_id!r} "
                f"not found in the {label} graph"
            )
        if anchor[0]["merged_into"] == canonical_id and anchor[0]["same_as"] > 0:
            return {"transferred": 0, "skipped_duplicates": 0, "same_as": 0, "already_merged": True}
        if anchor[0]["merged_into"] is not None and anchor[0]["merged_into"] != canonical_id:
            raise RuntimeError(
                f"candidate {candidate_id!r} is already merged into "
                f"{anchor[0]['merged_into']!r}"
            )
        # Tombstoned into THIS canonical but SAME_AS missing (pre-existing
        # partial state): resume — transfers are idempotent and we re-create
        # the provenance edge.
        resumed = anchor[0]["merged_into"] == canonical_id

        collect_out = run(
            f"MATCH (cand:{label} {{id:$cid}})-[r]->(x) "
            "WHERE type(r) <> 'SAME_AS' AND elementId(x) <> elementId(cand) "
            "RETURN type(r) AS t, count(r) AS total",
            cid=candidate_id,
        )
        collect_in = run(
            f"MATCH (x)-[r]->(cand:{label} {{id:$cid}}) "
            "WHERE type(r) <> 'SAME_AS' "
            "RETURN type(r) AS t, count(r) AS total",
            cid=candidate_id,
        )

        transferred = 0
        skipped = 0
        # Each transfer stays pattern-based (driven by the candidate's existing
        # relationships) so the planner resolves targets through indexes instead
        # of scanning the graph for elementIds.
        for rel in collect_out:
            t = _validated(rel["t"])
            created = run(
                f"MATCH (canon:{label} {{id:$xid}}) "
                f"MATCH (cand:{label} {{id:$cid}})-[r:{t}]->(x) "
                f"WHERE NOT EXISTS((canon)-[:{t}]->(x)) "
                f"CREATE (canon)-[nr:{t}]->(x) SET nr = properties(r) "
                "RETURN count(nr) AS n",
                xid=canonical_id,
                cid=candidate_id,
            )[0]["n"]
            transferred += created
            skipped += rel["total"] - created
        for rel in collect_in:
            t = _validated(rel["t"])
            created = run(
                f"MATCH (canon:{label} {{id:$xid}}) "
                f"MATCH (x)-[r:{t}]->(cand:{label} {{id:$cid}}) "
                f"WHERE NOT EXISTS((x)-[:{t}]->(canon)) "
                f"CREATE (x)-[nr:{t}]->(canon) SET nr = properties(r) "
                "RETURN count(nr) AS n",
                xid=canonical_id,
                cid=candidate_id,
            )[0]["n"]
            transferred += created
            skipped += rel["total"] - created

        run(
            f"MATCH (cand:{label} {{id:$cid}})-[r]-(x) "
            "WHERE type(r) <> 'SAME_AS' DELETE r",
            cid=candidate_id,
        )
        run(
            f"MATCH (cand:{label} {{id:$cid}}), (canon:{label} {{id:$xid}}) "
            "MERGE (cand)-[sa:SAME_AS]->(canon) "
            "ON CREATE SET sa.source = 'review', sa.confidence = $conf, "
            "sa.resolution_id = $rid, sa.merged_at = datetime() "
            "SET cand.merged_into = $xid",
            cid=candidate_id,
            xid=canonical_id,
            conf=round(confidence, 4),
            rid=resolution_id,
        )
        return {
            "transferred": transferred,
            "skipped_duplicates": skipped,
            "same_as": 1,
            "already_merged": False,
            "resumed": resumed,
        }


def _validated(rel_type: str) -> str:
    if not _RELTYPE_RE.match(rel_type):
        raise ValueError(f"refusing to interpolate relationship type {rel_type!r} into Cypher")
    return rel_type