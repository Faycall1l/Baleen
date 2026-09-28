"""``BoundedSeen`` dedup window: cap eviction and invalid caps.

Part of the ingestion suite split out of the former ``tests/test_ingestion.py`` (M4).
"""

from __future__ import annotations


def test_bounded_seen_evicts_oldest_issues_27():
    """#27: the seen-set is capped; evicted keys can be re-remembered."""
    from app.ingestion.source import BoundedSeen

    seen = BoundedSeen(cap=3)
    seen.remember("a")
    seen.remember("b")
    seen.remember("c")
    assert "a" in seen and "b" in seen and "c" in seen

    seen.remember("d")  # evicts "a"
    assert "a" not in seen
    assert "b" in seen and "c" in seen and "d" in seen

    seen.remember("a")  # earliest surviving entry ("b") is evicted next
    assert "b" not in seen
    assert "a" in seen

    # Re-remembering an existing key is a no-op (doesn't reorder eviction).
    seen.remember("c")
    seen.remember("zzz")
    assert "d" in seen and "a" in seen and "zzz" in seen
    assert "c" not in seen  # "logically oldest" surviving entry was evicted


def test_bounded_seen_rejects_invalid_cap():
    from app.ingestion.source import BoundedSeen

    try:
        BoundedSeen(cap=0)
    except ValueError:
        pass
    else:
        raise AssertionError("BoundedSeen(cap=0) must raise")
