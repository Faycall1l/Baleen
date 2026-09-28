"""Read-only Cypher bounding: LIMIT injection and aggregation short-circuit.

Part of the query suite split out of the former ``tests/test_query_agent.py`` (M3).
"""

from __future__ import annotations

from app.query.tools import bounded_query
from app.query.agent import QueryAgent
from tests.query.conftest import EmptyStore, stub_llm


def test_bounded_rejects_empty_query():
    agent = QueryAgent(llm=object(), store=object())  # type: ignore[arg-type]
    assert bounded_query("  ;   ") == ""


def test_bounded_appends_limit_when_missing():
    from app.query.agent import QueryAgent
    from app.query.tools import MAX_RESULT_ROWS

    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    bounded = bounded_query("MATCH (e:Event) RETURN e")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_bounded_keeps_existing_limit():
    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    query = "MATCH (e:Event) RETURN e ORDER BY e.event_time DESC LIMIT 10"
    assert bounded_query(query) == query


def test_bounded_leaves_aggregations_alone():
    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    query = (
        "MATCH (e:Event)-[:INVOLVES_TICKER]->(t:Ticker) "
        "RETURN t.ticker AS ticker, count(e) AS n ORDER BY n DESC"
    )
    assert bounded_query(query) == query


def test_bounded_strips_trailing_semicolon():
    from app.query.tools import MAX_RESULT_ROWS

    agent = QueryAgent(llm=stub_llm(), store=EmptyStore())
    bounded = bounded_query("MATCH (e:Event) RETURN e;")
    assert bounded.endswith(f"LIMIT {MAX_RESULT_ROWS}")


def test_agent_caps_unbounded_query_at_execution():
    from app.query.tools import MAX_RESULT_ROWS

    captured = {}

    class RecordingStore:
        def run_read(self, cypher, **params):
            captured["cypher"] = cypher
            return [{"e": "row"}]

    agent = QueryAgent(
        llm=stub_llm(cypher="MATCH (e:Event) RETURN e"),
        store=RecordingStore(),
    )
    result = agent.ask("Show me all events.")

    assert captured["cypher"].endswith(f"LIMIT {MAX_RESULT_ROWS}")
    assert result.cypher == captured["cypher"]
    assert result.rows == [{"e": "row"}]
    assert result.answer
