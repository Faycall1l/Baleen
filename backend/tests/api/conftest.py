"""Shared fixtures/helpers for the HTTP API suite.

Autouse here (not in the top-level conftest) so they only scope the API tests:
every ``TestClient`` boot runs app lifespan, and the ``/api/graph`` payload
cache is module-level, so without these the suite is slow, flaky, and leaky.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore


@pytest.fixture(autouse=True)
def _clear_graph_cache():
    """The /api/graph payload cache is module-level and keyed by ``limit``;
    without clearing it, one test's payload (real or boom-mock) leaks into the
    next test's assertions."""
    from app.api import routes
    from app.api import signals_routes

    routes._graph_cache.clear()
    signals_routes._signals_cache.clear()
    yield
    routes._graph_cache.clear()
    signals_routes._signals_cache.clear()


@pytest.fixture(autouse=True)
def _quiet_lifespan(monkeypatch):
    """Every TestClient boot runs app lifespan: with stock settings that spins
    up the real background scheduler (immediate ingest tick -> LLM calls with
    90s timeouts) and seeds alert rules, which made test_api take 40+ minutes
    and flake on a loaded box (E6). Default every test to scheduler/seed off;
    tests that need the scheduler (lifespan start/stop) or secure settings
    override via their own monkeypatch, which stacks after this fixture."""
    from app.config import get_settings

    quiet = get_settings().model_copy(
        update={"scheduler_enabled": False, "seed_default_alerts": False}
    )
    monkeypatch.setattr("app.main.get_settings", lambda: quiet)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: quiet)


def _client():
    from app.main import app

    return TestClient(app)


def _services_available() -> bool:
    from app.config import get_settings

    settings = get_settings()
    try:
        store = PostgresStore(settings.postgres_dsn)
        store.engine.connect()
        neo4j = Neo4jStore(settings)
        neo4j.run("RETURN 1")
        neo4j.close()
        store.engine.dispose()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Failure-injection doubles shared by the endpoint error-path tests
# ---------------------------------------------------------------------------


class _Neo4jBoom:
    def ensure_indexes(self):
        pass

    def run(self, *a, **k):
        raise RuntimeError("neo4j exploded")

    def run_read(self, *a, **k):
        raise RuntimeError("neo4j exploded")


class _StoreBoom:
    def __init__(self, *a, **k):
        pass

    def ensure_rule(self, *a, **k):
        pass

    def get_alerts(self, **k):
        raise RuntimeError("postgres exploded")

    def get_reports(self, **k):
        raise RuntimeError("postgres exploded")

    def get_leaderboard(self, **k):
        raise RuntimeError("postgres exploded")

    def get_reports_since(self, after):
        raise RuntimeError("postgres exploded")

    def get_alerts_since(self, after):
        raise RuntimeError("postgres exploded")

    class engine:
        def dispose(self):
            raise RuntimeError("dispose exploded")


def _quiet_settings(monkeypatch):
    """Point both main.lifespan and routes at settings with the scheduler and
    alert seeding switched off, so failure-path tests don't spin up live jobs."""
    from app.config import get_settings

    quiet = get_settings().model_copy(
        update={"scheduler_enabled": False, "seed_default_alerts": False}
    )
    monkeypatch.setattr("app.main.get_settings", lambda: quiet)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: quiet)
    return quiet


def _boom_app(monkeypatch) -> None:
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(routes, "_get_neo4j", lambda: _Neo4jBoom())
    monkeypatch.setattr(routes, "_get_store", lambda: _StoreBoom())
