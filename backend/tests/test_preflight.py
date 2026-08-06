"""Preflight readiness checks: config validation and probe wiring."""

from __future__ import annotations

import httpx

from app.config import Settings
from scripts.preflight import (
    check_llm,
    check_scheduler,
    check_x_api,
    run_checks,
)


def _settings(**overrides) -> Settings:
    defaults = {
        "x_api_bearer_token": "real-x-bearer-token",
        "llm_model_name": "real-vllm-model",
    }
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def test_x_api_passes_with_real_token():
    detail = check_x_api(_settings())
    assert "token set" in detail


def test_x_api_fails_on_placeholder_token():
    from app.config import PLACEHOLDER_TOKENS

    for token in PLACEHOLDER_TOKENS:
        settings = _settings(x_api_bearer_token=token)
        try:
            check_x_api(settings)
        except RuntimeError as exc:
            assert "X_API_BEARER_TOKEN" in str(exc)
        else:  # pragma: no cover - should always raise
            raise AssertionError(f"placeholder token {token!r} was accepted")


def test_x_api_fails_on_empty_handles():
    import pytest

    with pytest.raises(RuntimeError, match="empty"):
        check_x_api(_settings(x_user_handles=" , "))


def test_llm_fails_on_placeholder_model():
    import pytest

    with pytest.raises(RuntimeError, match="placeholder"):
        check_llm(_settings(llm_model_name="your-model-name"))


def test_llm_probes_models_endpoint(monkeypatch):
    class _Response:
        status_code = 200

        def raise_for_status(self):
            pass

    def fake_get(url, timeout):
        assert url == "http://localhost:9999/v1/models"
        return _Response()

    monkeypatch.setattr(httpx, "get", fake_get)
    detail = check_llm(_settings(llm_base_url="http://localhost:9999/v1"))
    assert "reachable" in detail


def test_llm_reports_non_vllm_port(monkeypatch):
    """A 404 from an unrelated server on the port must be reported as FAIL —
    this is the classic LLM_BASE_URL misconfiguration."""
    import pytest

    class _Response:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(httpx, "get", lambda url, timeout: _Response())
    with pytest.raises(RuntimeError, match="404"):
        check_llm(_settings(llm_base_url="http://localhost:9999/v1"))


def test_scheduler_fails_when_disabled():
    import pytest

    with pytest.raises(RuntimeError, match="SCHEDULER_ENABLED"):
        check_scheduler(_settings(scheduler_enabled=False))


def test_run_checks_aggregates_pass_and_fail(monkeypatch):
    class _Response:
        status_code = 200
        text = ""

    monkeypatch.setattr(httpx, "get", lambda url, timeout: _Response())

    settings = _settings(scheduler_enabled=True)
    results = run_checks(settings)

    names = {r["check"] for r in results}
    assert names == {"x_api", "llm", "postgres", "neo4j", "scheduler"}
    # x_api + llm + scheduler are config-only (llm probe stubbed) and must pass.
    by_name = {r["check"]: r for r in results}
    assert by_name["x_api"]["status"] == "pass"
    assert by_name["llm"]["status"] == "pass"
    assert by_name["scheduler"]["status"] == "pass"
    # postgres/neo4j may be down in this environment; both must be reported
    # as a concrete pass/fail rather than crashing the whole run.
    assert by_name["postgres"]["status"] in {"pass", "fail"}
    assert by_name["neo4j"]["status"] in {"pass", "fail"}
