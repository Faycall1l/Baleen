"""NUL-byte scrubbing shared by the Postgres store mixins (K2).

Postgres text cannot hold NUL bytes, so a single poisoned row from a hostile
source would otherwise abort an entire ingest batch.
"""

from __future__ import annotations


def _strip_nulls(value: str) -> str:
    """Postgres text cannot hold NUL bytes (``\\x00``); strip them so a
    poisoned row from a hostile source cannot abort an entire ingest batch."""
    if "\x00" in value:
        return value.replace("\x00", "")
    return value

def _scrub_nulls(value):
    """Recursively strip actual NUL bytes (``\\x00``) from JSON payloads.

    Applied *before* ``json.dumps``: Postgres can neither store a NUL byte nor
    decode one from a ``\\u0000`` escape in a JSON document, and a single
    poisoned row would otherwise raise ``DataError`` and roll back the whole
    batch. Doing this at the value layer (instead of on the encoded string)
    leaves text that *literally* reads ``\\u0000`` untouched, since that is
    valid JSON text and not a control character.
    """
    if isinstance(value, str):
        return value.replace("\x00", "") if "\x00" in value else value
    if isinstance(value, list):
        return [_scrub_nulls(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_nulls(v) for k, v in value.items()}
    return value
