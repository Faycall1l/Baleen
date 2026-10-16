"""Committee endpoint: sectors, members, and member trades in one read.

Part of the API suite; the engine is faked so no live services are needed.
"""

from __future__ import annotations

from tests.api.conftest import (
    _client,
    _quiet_settings,
)


def _card():
    return {
        "id": "HSBA",
        "name": "Financial Services",
        "sectors": ["Financials"],
        "members": [{"id": "W000187", "name": "Maxine Waters"}],
        "trades": [{"event_id": "evt_c", "ticker": "NVDA"}],
    }


def test_committee_returns_card(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Committee:
        @staticmethod
        def committee(cid):
            # The route forwards the raw path segment; normalization is the
            # engine's job (covered in test_signals.py).
            assert cid == "hsba"
            return _card()

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Committee())
    with _client() as c:
        body = c.get("/api/committees/hsba").json()
    assert body["committee"]["name"] == "Financial Services"
    assert body["committee"]["sectors"] == ["Financials"]
    assert body["committee"]["members"][0]["id"] == "W000187"
    assert body["committee"]["trades"][0]["event_id"] == "evt_c"


def test_committee_unknown_is_404(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Nobody:
        @staticmethod
        def committee(cid):
            return None

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Nobody())
    with _client() as c:
        res = c.get("/api/committees/ZZZ")
        assert res.status_code == 404
        assert res.json()["detail"] == "unknown committee"


def test_committee_error_is_json_500(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Boom:
        @staticmethod
        def committee(cid):
            raise RuntimeError("committee exploded")

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Boom())
    with _client() as c:
        res = c.get("/api/committees/HSBA")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("committee fetch failed")
