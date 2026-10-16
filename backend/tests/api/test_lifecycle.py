"""App lifecycle: health, engine wiring, scheduler start/stop, and clean shutdown.

Part of the API suite split out of the former ``tests/test_api.py``.
"""

from __future__ import annotations

import pytest

from tests.api.conftest import _client, _quiet_settings, _services_available


def test_health():
    with _client() as c:
        res = c.get("/health")
        assert res.json()["app"] == "baleen"
        if not _services_available():
            assert res.status_code == 503
            return
        assert res.status_code == 200
        assert res.json()["status"] == "ok"
        checks = res.json()["checks"]
        assert {"postgres", "neo4j"} <= set(checks)
        assert checks["postgres"]["status"] == "ok"
        assert checks["neo4j"]["status"] == "ok"
        if "llm" in checks:  # present iff a real model name is configured
            assert checks["llm"]["status"] == "ok"


def test_health_reports_llm_down_when_configured(monkeypatch):
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.main as main
    from app.config import Settings

    class DownLLM:
        base_url = "http://llm/v1"

        def health(self):
            return False

        def healthy(self):
            return False

    # A real model name is configured, so /health must probe the LLM.
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: Settings(_env_file=None, llm_model_name="Meta-Llama-3-8B"),
    )
    monkeypatch.setattr(main.routes, "_get_llm", lambda: DownLLM())

    with _client() as c:
        res = c.get("/health")
        body = res.json()
        assert res.status_code == 503
        assert body["checks"]["llm"]["status"] == "down"
        assert body["checks"]["postgres"]["status"] == "ok"


def test_app_lifecycle_does_not_leak_extra_postgres_engine(monkeypatch):
    """Startup must reuse the shared store (routes._get_store) so each app
    lifecycle disposes exactly the engines it created."""
    if not _services_available():
        pytest.skip("postgres/neo4j not available")

    import app.api.routes as routes
    import app.db.postgres as postgres_module

    created = {"n": 0}
    original = postgres_module.PostgresStore

    class CountingStore(original):
        def __init__(self, dsn):
            created["n"] += 1
            super().__init__(dsn)

    monkeypatch.setattr(postgres_module, "PostgresStore", CountingStore)
    monkeypatch.setattr(routes, "PostgresStore", CountingStore)
    monkeypatch.setattr(routes, "_store", None)  # force lazy creation this cycle

    with _client() as c:
        assert c.get("/health").status_code == 200

    assert created["n"] == 1


def test_pipeline_writer_is_wired_with_entity_resolver(postgres_store, monkeypatch):
    """The production pipeline's GraphWriter must resolve Politician/Insider
    names through the alias registry, sharing the store instance (no second
    engine pool). Selective: needs only real Postgres; the Neo4j driver is
    constructed lazily and never queried here."""
    import app.api.routes as routes
    from app.graph.resolution import EntityResolver

    monkeypatch.setattr(routes, "_get_store", lambda: postgres_store)
    monkeypatch.setattr(routes, "_store", None)
    monkeypatch.setattr(routes, "_neo4j", None)
    monkeypatch.setattr(routes, "_llm", None)

    pipeline = routes._get_processing()
    try:
        resolver = pipeline.writer.entity_resolver
        assert isinstance(resolver, EntityResolver)
        assert resolver._db is postgres_store
    finally:
        routes.close_services()


def test_close_services_survives_close_failures(monkeypatch):
    import app.api.routes as routes

    class _BadNeo4j:
        def close(self):
            raise RuntimeError("close exploded")

    class _BadStore:
        class engine:
            def dispose(self):
                raise RuntimeError("dispose exploded")

    class _BadLLM:
        def close(self):
            raise RuntimeError("llm close exploded")

    monkeypatch.setattr(routes, "_neo4j", _BadNeo4j())
    monkeypatch.setattr(routes, "_store", _BadStore())
    monkeypatch.setattr(routes, "_llm", _BadLLM())

    routes.close_services()  # must not raise

    assert routes._neo4j is None and routes._store is None and routes._llm is None


def test_engine_factories_wire_real_dependencies(monkeypatch):
    """The lazy factories build production engines against whatever getters are
    wired — here stubbed out, so no DB clients are constructed."""
    import app.api.routes as routes

    _quiet_settings(monkeypatch)
    monkeypatch.setattr(routes, "_get_store", lambda: object())
    monkeypatch.setattr(routes, "_get_neo4j", lambda: object())
    monkeypatch.setattr(routes, "_get_llm", lambda: object())

    from app.alerts.engine import AlertEngine
    from app.pipeline import ProcessingPipeline
    from app.reports.engine import ReportEngine

    assert isinstance(routes.get_alert_engine(), AlertEngine)
    assert isinstance(routes.get_report_engine(), ReportEngine)
    assert isinstance(routes._get_processing(), ProcessingPipeline)


def test_lifespan_starts_and_stops_scheduler(monkeypatch):
    """With scheduler_enabled=True the lifespan boots and later stops a real
    (here stubbed) scheduler instance."""
    captured = {"started": False, "stopped": False}

    class _Recorder:
        def start(self):
            captured["started"] = True

        def stop(self):
            captured["stopped"] = True

    enabled = _quiet_settings(monkeypatch).model_copy(update={"scheduler_enabled": True})
    monkeypatch.setattr("app.main.get_settings", lambda: enabled)
    monkeypatch.setattr("app.api.routes.get_settings", lambda: enabled)
    monkeypatch.setattr("app.main._build_scheduler", lambda s: _Recorder())

    with _client() as c:
        try:
            assert c.get("/health").status_code == 200
        finally:
            pass

    assert captured["started"] is True
    assert captured["stopped"] is True
