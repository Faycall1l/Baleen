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


def test_x_max_pages_bounds_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_max_pages=0)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, x_max_pages=100)
    assert Settings(_env_file=None, x_max_pages=5).x_max_pages == 5
