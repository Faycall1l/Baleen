"""Mention normalization: case, punctuation, token order.

Part of the resolution suite split out of the former
``tests/test_resolution.py`` (M5). Everything runs against the real
``baleen_test`` Postgres and the shared Neo4j dev instance (conftest
fixtures) -- no mocks, in keeping with the research project's rigor bar.
"""

from __future__ import annotations

from app.graph.resolution import (
    normalize_mention,
)


class TestNormalizeMention:
    def test_folds_case_punctuation_and_whitespace(self):
        assert normalize_mention("  Rep. Nancy Pelosi!,  ") == "rep nancy pelosi"

    def test_dash_and_apostrophe_fold_to_space(self):
        assert normalize_mention("Jean-Luc D'Amore") == "jean luc d amore"

    def test_token_order_is_preserved(self):
        # Reversed order is a different name, not a spelling variant.
        assert normalize_mention("Nancy Pelosi") != normalize_mention("Pelosi Nancy")
