"""Person endpoint: identity, committees, and latest trades in one read.

Part of the API suite; the engine is faked so no live services are needed.
"""

from __future__ import annotations

from tests.api.conftest import (
    _quiet_settings,
    _client,
)


def _person():
    return {
        "id": "W000817",
        "name": "Elizabeth Warren",
        "chamber": "senate",
        "party": "D",
        "committees": [{"id": "SSBK", "name": "Banking"}],
        "trades": [
            {
                "event_id": "evt_1",
                "ticker": "NVDA",
                "side": "sell",
                "date": "2026-09-01",
                "amount": "$1,001 - $15,000",
                "source_url": "https://x.com/y",
            }
        ],
    }


def test_politician_returns_card(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _People:
        @staticmethod
        def person(politician_id):
            assert politician_id == "W000817"
            return _person()

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _People())
    with _client() as c:
        body = c.get("/api/politicians/W000817").json()
    assert body["person"]["name"] == "Elizabeth Warren"
    assert body["person"]["committees"] == [{"id": "SSBK", "name": "Banking"}]
    assert body["person"]["trades"][0]["ticker"] == "NVDA"


def test_politician_unknown_is_404(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Nobody:
        @staticmethod
        def person(politician_id):
            return None

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Nobody())
    with _client() as c:
        res = c.get("/api/politicians/NOPE")
        assert res.status_code == 404
        assert res.json()["detail"] == "unknown politician"


def test_politician_error_is_json_500(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Boom:
        @staticmethod
        def person(politician_id):
            raise RuntimeError("person exploded")

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Boom())
    with _client() as c:
        res = c.get("/api/politicians/W000817")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("person fetch failed")
