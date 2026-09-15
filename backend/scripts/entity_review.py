"""Human-in-the-loop review CLI for the entity-resolution queue.

The fuzzy near-duplicate pass never merges anything automatically; it queues
ambiguous pairs (gray band 0.85-0.95, plus >=0.95 collisions across different
ids and exact-spelling alias clashes) for a human decision. This script is that
decision surface — review pending suggestions and confirm or reject each one.

Usage:
    python -m scripts.entity_review --suggest               # run the fuzzy pass, queue new pairs
    python -m scripts.entity_review --list                  # show pending reviews
    python -m scripts.entity_review --suggest Politician    # run the pass for one entity type
    python -m scripts.entity_review --approve 12            # confirm suggestion #12
    python -m scripts.entity_review --reject 12             # reject suggestion #12
    python -m scripts.entity_review --list --raw            # print a copy-pasteable CSV

Exit codes: 0 on success, 1 on runtime errors (unknown id, no DB).
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys

from app.config import get_settings
from app.db.postgres import PostgresStore
from app.graph.resolution import ADMINISTERED_TYPES, EntityResolver

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _columns(headers: list[str], row: dict) -> list[str]:
    return [str(row.get(h, "-")) for h in headers]


def _print_table(headers: list[str], rows: list[dict], raw: bool = False) -> None:
    if raw:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(_columns(headers, row))
        sys.stdout.write(buf.getvalue().rstrip() + "\n")
        return
    if not rows:
        print("(nothing pending)")
        return
    rendered = [_columns(headers, row) for row in rows]
    widths = [len(h) for h in headers]
    for row in rendered:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for row in rendered:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _display_rows(rows: list[dict]) -> list[dict]:
    """Map DB column names to friendlier, review-oriented display keys."""
    display: list[dict] = []
    for row in rows:
        display.append(
            {
                "id": row["id"],
                "entity_type": row["entity_type"],
                "candidate": f"{row['candidate_name']} ({row['candidate_id']})",
                "canonical": f"{row['canonical_name']} ({row['canonical_id']})",
                "confidence": f"{row['confidence']:.3f}",
                "reason": row["reason"],
                "created_at": str(row["created_at"]),
            }
        )
    return display


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    actions = parser.add_argument_group("actions")
    actions.add_argument("--suggest", action="store_true",
                         help="run the fuzzy near-duplicate pass and queue new pairs")
    actions.add_argument("--list", action="store_true", help="show pending reviews")
    actions.add_argument("--approve", type=int, metavar="ID", help="confirm a pending review")
    actions.add_argument("--reject", type=int, metavar="ID", help="reject a pending review")
    parser.add_argument("entity_type", nargs="?", choices=ADMINISTERED_TYPES,
                        help="restrict --suggest to one entity type")
    parser.add_argument("--raw", action="store_true", help="tab-compatible CSV output")
    parser.add_argument("--dsn", default=None,
                        help="Postgres DSN (default: settings-database-dsn)")
    args = parser.parse_args(argv)

    if not (args.suggest or args.list or args.approve or args.reject):
        parser.error("one of --suggest/--list/--approve/--reject is required")

    dsn = args.dsn or os.getenv("TEST_DATABASE_URL") or get_settings().postgres_dsn
    store = PostgresStore(dsn)
    resolver = EntityResolver(store)
    try:
        store.init_schema()

        if args.suggest:
            entity_types = (args.entity_type,) if args.entity_type else ADMINISTERED_TYPES
            total = 0
            for entity_type in entity_types:
                total += resolver.suggest_merges(entity_type)
            print(f"queued {total} new resolution pair(s) for review")

        if args.list:
            headers = ["id", "entity_type", "candidate", "canonical", "confidence", "reason"]
            _print_table(headers, _display_rows(resolver.pending_reviews()), raw=args.raw)

        if args.approve is not None:
            if resolver.approve(args.approve):
                print(f"approved review #{args.approve}")
                return 0
            print(f"review #{args.approve} not found or already resolved", file=sys.stderr)
            return 1

        if args.reject is not None:
            if resolver.reject(args.reject):
                print(f"rejected review #{args.reject}")
                return 0
            print(f"review #{args.reject} not found or already resolved", file=sys.stderr)
            return 1
        return 0
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())