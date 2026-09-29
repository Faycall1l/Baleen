"""Feed endpoints: alerts, reports, digest, leaderboard, and signals.

Part of the API suite split out of the former ``tests/test_api.py`` (M1).
"""

from __future__ import annotations

import pytest

from tests.api.conftest import (
    _quiet_settings,
    _boom_app,
    _client,
    _services_available,
)

def test_alerts_list():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/alerts").json()
        assert "alerts" in body and "count" in body
        assert body["count"] == len(body["alerts"])


def test_alerts_limit_is_bounded():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        assert c.get("/api/alerts?limit=0").status_code == 422
        assert c.get("/api/alerts?limit=1000").status_code == 422
        assert c.get("/api/alerts?limit=50").status_code == 200


def test_alerts_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/alerts")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("alerts fetch failed")


def test_reports_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/reports")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("reports fetch failed")


def test_leaderboard_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)
    with _client() as c:
        res = c.get("/api/leaderboard")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("leaderboard fetch failed")


def test_signals_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)

    class _SignalsBoom:
        def bundle(self, **k):
            raise RuntimeError("signals exploded")

        def similar(self, **k):
            raise RuntimeError("signals exploded")

        def flow(self, **k):
            raise RuntimeError("signals exploded")

    import app.api.routes as routes

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _SignalsBoom())
    with _client() as c:
        assert c.get("/api/signals").status_code == 500
        assert c.get("/api/signals/similar?politician_id=W000187").status_code == 500
        res = c.get("/api/signals/flow")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("options flow failed")


def test_digest_error_is_json_500(monkeypatch):
    _boom_app(monkeypatch)

    class _DigestBoom:
        def ensure_digest(self, **k):
            raise RuntimeError("digest exploded")

    import app.api.routes as routes

    monkeypatch.setattr(routes, "get_report_engine", lambda: _DigestBoom())
    with _client() as c:
        res = c.get("/api/reports/digest")
        assert res.status_code == 500
        assert res.json()["detail"].startswith("digest failed")


def test_signals_bundle_shape():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/signals?window_days=365").json()
        for key in ("momentum", "clusters", "options_meter", "uncovered", "suspicious"):
            assert isinstance(body[key], list)
        assert body["window_days"] == 365


def test_signals_similar_returns_traders():
    if not _services_available():
        pytest.skip("postgres/neo4j not available")
    with _client() as c:
        body = c.get("/api/signals/similar?politician_id=W000187").json()
        assert isinstance(body["similar"], list)


def test_signals_flow_success_path(monkeypatch):
    """The tape normalizes tickers, echoes filters, and reports its count."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    seen = {}

    class _Flow:
        @staticmethod
        def flow(**kwargs):
            seen.update(kwargs)
            return [
                {
                    "event_id": "evt_flow",
                    "ticker": "NVDA",
                    "side": "call",
                    "ratio": 5.0,
                    "volume": 5000,
                }
            ]

    monkeypatch.setattr(routes, "get_signals_engine", lambda: _Flow())
    with _client() as c:
        body = c.get(
            "/api/signals/flow?tickers=nvda,aapl&side=call&min_ratio=3"
            "&window_days=7&limit=5"
        ).json()

    assert body["count"] == 1
    assert body["flow"][0]["ticker"] == "NVDA"
    assert body["filters"] == {
        "tickers": ["NVDA", "AAPL"],
        "side": "call",
        "min_ratio": 3.0,
        "window_days": 7,
        "limit": 5,
    }
    assert seen["tickers"] == ["NVDA", "AAPL"]


def test_reports_digest_leaderboard_success_paths(monkeypatch):
    """The happy-path returns for /api/reports, /api/reports/digest and
    /api/leaderboard (the error 500s are already covered elsewhere)."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)

    class _Store:
        @staticmethod
        def get_reports(limit=20, kind=None):
            return ["brief-row"] if kind is None else [f"{kind}-row"]

        @staticmethod
        def get_leaderboard(limit=20):
            return [{"politician": "Zed Zed"}]

    monkeypatch.setattr(routes, "_get_store", lambda: _Store())

    class _Digest:
        @staticmethod
        def ensure_digest(period=None):
            return {"id": 8, "period": "2026-09-14"}

    monkeypatch.setattr(routes, "get_report_engine", lambda: _Digest())

    with _client() as c:
        all_reports = c.get("/api/reports").json()
        assert all_reports == {"reports": ["brief-row"], "count": 1}

        digests = c.get("/api/reports?kind=digest").json()
        assert digests["reports"] == ["digest-row"]

        digest = c.get("/api/reports/digest").json()
        assert digest["report"]["id"] == 8

        leaders = c.get("/api/leaderboard").json()
        assert leaders["count"] == 1 and leaders["leaders"][0]["politician"] == "Zed Zed"
