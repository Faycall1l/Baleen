"""Ticker endpoint: sector, flow prints, and trades in one read.

Part of the API suite; the engine is faked so no live services are needed.
"""

from __future__ import annotations

from tests.api.conftest import (
    _quiet_settings,
    _client,
)


def _card():
    return {
        "ticker": "NVDA",
        "sector": "Technology",
        "trades": [{"event_id": "evt_t", "ticker": "NVDA"}],
        "flow": [{"event_id": "evt_f", "side": "call"}],
    }


def test_ticker_returns_card(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Market:
        @staticmethod
        def ticker(sym):
            # The route forwards the raw path segment; normalization is the
            # engine's job (covered in test_signals.py).
            assert sym == "nvda"
            return _card()

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Market())
    with _client() as c:
        body = c.get("/api/tickers/nvda").json()
    assert body["ticker"]["sector"] == "Technology"
    assert body["ticker"]["trades"][0]["event_id"] == "evt_t"
    assert body["ticker"]["flow"][0]["side"] == "call"


def test_ticker_unknown_is_404(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Nobody:
        @staticmethod
        def ticker(sym):
            return None

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Nobody())
    with _client() as c:
        res = c.get("/api/tickers/ZZZ")
        assert res.status_code == 404
        assert res.json()["detail"] == "unknown ticker"


def test_ticker_error_is_json_500(monkeypatch):
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Boom:
        @staticmethod
        def ticker(sym):
            raise RuntimeError("ticker exploded")

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Boom())
    with _client() as c:
        res = c.get("/api/tickers/NVDA")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("ticker fetch failed")
