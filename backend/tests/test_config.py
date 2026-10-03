"""Settings validation: bad env values fail fast at construction."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_invalid_llm_tool_mode_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_tool_mode="yolo")


def test_out_of_range_confidence_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, confidence_threshold=1.5)


def test_negative_retries_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_max_retries=-1)


def test_llm_timeout_bounds_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=1)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_timeout_seconds=10000)
    assert Settings(_env_file=None, llm_timeout_seconds=120).llm_timeout_seconds == 120


def test_llm_base_url_is_trailing_slash_stripped():
    settings = Settings(_env_file=None, llm_base_url="http://localhost:8001/v1/")
    assert settings.llm_base_url == "http://localhost:8001/v1"


def test_x_api_configured_treats_placeholders_as_unconfigured():
    for token in ("", "placeholder-bearer-token", "your-x-api-bearer-token"):
        assert Settings(_env_file=None, x_api_bearer_token=token).x_api_configured is False
    assert Settings(_env_file=None, x_api_bearer_token="real-token-value").x_api_configured is True


def test_llm_configured_treats_placeholders_as_unconfigured():
    for name in ("", "placeholder-model", "your-model-name"):
        assert Settings(_env_file=None, llm_model_name=name).llm_configured is False
    assert Settings(_env_file=None, llm_model_name="Meta-Llama-3-8B-Instruct").llm_configured is True


def test_process_batch_size_bounds_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, process_batch_size=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, process_batch_size=5000)
    assert Settings(_env_file=None, process_batch_size=250).process_batch_size == 250


def test_x_max_pages_bounds_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_max_pages=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_max_pages=100)
    assert Settings(_env_file=None, x_max_pages=5).x_max_pages == 5


def test_x_api_timeout_bounds_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_api_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_api_timeout_seconds=10000)
    assert Settings(_env_file=None, x_api_timeout_seconds=60).x_api_timeout_seconds == 60


def test_missing_url_scheme_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url="localhost:8001/v1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_base_url="ftp://localhost:8001/v1")
    assert Settings(_env_file=None, llm_base_url="http://host.docker.internal:8001/v1").llm_base_url == "http://host.docker.internal:8001/v1"


def test_bolt_scheme_required():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, neo4j_uri="http://localhost:7687")
    assert Settings(_env_file=None, neo4j_uri="bolt+s://localhost:7687").neo4j_uri == "bolt+s://localhost:7687"
    assert Settings(_env_file=None, neo4j_read_uri="").neo4j_read_uri == ""


def test_database_url_scheme_required():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="sqlite:///x.db")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="neo4j://localhost:7687")
    assert Settings(_env_file=None, database_url="postgresql+psycopg://u:p@localhost:5432/b").database_url == "postgresql+psycopg://u:p@localhost:5432/b"


def test_postgres_dsn_prefers_explicit_database_url():
    assert (
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db").postgres_dsn
        == "postgresql+psycopg://u:p@h/db"
    )
    dsn = Settings(_env_file=None).postgres_dsn
    assert dsn.startswith("postgresql+psycopg://")


def test_x_handles_splits_and_trims():
    assert Settings(_env_file=None, x_user_handles="a, b ,,c").x_handles == ["a", "b", "c"]
