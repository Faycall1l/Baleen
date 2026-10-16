"""Query endpoint: success payload and JSON 500 on LLM/store failure.

Part of the API suite split out of the former ``tests/test_api.py``.
"""

from __future__ import annotations

from tests.api.conftest import (
    _client,
    _quiet_settings,
)


def test_query_route_returns_payload(monkeypatch):
    """/api/query happy path with a stubbed agent: no live LLM needed."""
    import app.api.routes as routes

    class FakeAgent:
        def __init__(self, llm, store):
            self.llm, self.store = llm, store

        def ask(self, question):
            return type(
                "Res",
                (),
                {
                    "as_dict": lambda self: {
                        "answer": "a",
                        "rows": [{"ticker": "NVDA"}],
                        "sources": [],
                        "allowed": True,
                        "has_data": True,
                        "error": None,
                    }
                },
            )()

    monkeypatch.setattr(routes, "QueryAgent", FakeAgent)
    monkeypatch.setattr(routes, "_get_llm", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    _quiet_settings(monkeypatch)

    with _client() as c:
        res = c.post("/api/query", json={"question": "What did Warren buy?"})
        assert res.status_code == 200
        assert res.json()["rows"] == [{"ticker": "NVDA"}]


def test_query_route_error_is_json_500(monkeypatch):
    import app.api.routes as routes

    class BoomAgent:
        def __init__(self, llm, store):
            pass

        def ask(self, question):
            raise RuntimeError("agent exploded")

    monkeypatch.setattr(routes, "QueryAgent", BoomAgent)
    monkeypatch.setattr(routes, "_get_llm", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    _quiet_settings(monkeypatch)

    with _client() as c:
        res = c.post("/api/query", json={"question": "hi"})
        assert res.status_code == 500
        assert res.json()["detail"].startswith("query agent failed")
