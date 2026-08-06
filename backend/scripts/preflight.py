"""Preflight readiness check for a live Baleen run.

Validates every external dependency and config value a live trial needs, with
clear PASS/FAIL lines and a process exit code (0 = ready). Run from
``backend/``:

    .venv/bin/python -m scripts.preflight

Placeholder credentials are reported as FAIL so a live run cannot start
half-configured. The X API is validated by configuration only (a probe call
costs a rate limit); the LLM is probed with ``GET /models``, which also catches
the classic mistake of pointing ``LLM_BASE_URL`` at a port serving something
that is not vLLM.
"""

from __future__ import annotations

import sys

import httpx

from app.config import Settings, get_settings
from app.db.neo4j import Neo4jStore
from app.db.postgres import PostgresStore


# ---------------------------------------------------------------------------
# Individual checks: each returns a short detail string or raises RuntimeError
# with an actionable message.
# ---------------------------------------------------------------------------


def check_x_api(settings: Settings) -> str:
    if not settings.x_api_configured:
        raise RuntimeError(
            "X_API_BEARER_TOKEN is a placeholder; set a real X API v2 bearer "
            "token in the repo-root .env"
        )
    handles = settings.x_handles
    if not handles:
        raise RuntimeError("X_USER_HANDLES is empty; nothing to poll")
    return (
        f"token set; {len(handles)} handle(s): {', '.join(handles)} "
        f"(window {settings.x_search_window_seconds}s, up to {settings.x_max_pages} pages)"
    )


def check_llm(settings: Settings) -> str:
    if not settings.llm_configured:
        raise RuntimeError(
            f"LLM_MODEL_NAME is a placeholder ({settings.llm_model_name!r}); "
            "set the real vLLM model name in .env"
        )
    url = f"{settings.llm_base_url}/models"
    try:
        response = httpx.get(url, timeout=min(settings.llm_timeout_seconds, 10))
    except httpx.HTTPError as exc:
        raise RuntimeError(f"LLM endpoint unreachable ({url}): {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(
            f"LLM probe GET {url} returned {response.status_code} "
            f"({response.text[:120]}). Is something else (not vLLM) on that port?"
        )
    return f"reachable at {settings.llm_base_url} (model {settings.llm_model_name})"


def check_postgres(settings: Settings) -> str:
    store = PostgresStore(settings.postgres_dsn)
    try:
        from sqlalchemy import text

        with store.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise RuntimeError(f"Postgres unreachable: {exc}") from exc
    finally:
        store.engine.dispose()
    return f"reachable ({settings.postgres_dsn.split('@')[-1]})"


def check_neo4j(settings: Settings) -> str:
    store = Neo4jStore(settings)
    try:
        store.run("RETURN 1")
    except Exception as exc:
        raise RuntimeError(f"Neo4j unreachable: {exc}") from exc
    finally:
        store.close()
    return f"reachable ({settings.neo4j_uri})"


def check_scheduler(settings: Settings) -> str:
    if not settings.scheduler_enabled:
        raise RuntimeError(
            "SCHEDULER_ENABLED is false; the app will not ingest or process "
            "automatically (use scripts/ingest_once.py for a manual run)"
        )
    return (
        f"enabled; tick every {settings.poll_interval_seconds}s, "
        f"batch {settings.process_batch_size} tweet(s)"
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_checks(settings: Settings) -> list[dict]:
    """Return a list of ``{check, status, detail}`` dicts, one per check."""
    checks = [
        ("x_api", check_x_api),
        ("llm", check_llm),
        ("postgres", check_postgres),
        ("neo4j", check_neo4j),
        ("scheduler", check_scheduler),
    ]
    results = []
    for name, fn in checks:
        try:
            results.append({"check": name, "status": "pass", "detail": fn(settings)})
        except Exception as exc:  # noqa: BLE001 - report, don't abort
            results.append({"check": name, "status": "fail", "detail": str(exc)})
    return results


def main() -> int:
    settings = get_settings()
    results = run_checks(settings)
    width = max(len(r["check"]) for r in results)
    for result in results:
        marker = "PASS" if result["status"] == "pass" else "FAIL"
        print(f"[{marker:4}] {result['check']:<{width}}  {result['detail']}")

    failed = [r["check"] for r in results if r["status"] != "pass"]
    if failed:
        print(f"\nNot ready: {', '.join(failed)}.")
        return 1
    print("\nReady for a live run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
