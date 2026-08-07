from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Root .env lives next to backend/ (repo root); resolve it absolutely so the
#: loaded file does not depend on the process working directory.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

#: Placeholder X API tokens (config defaults / .env.example) are treated as
#: "not configured" so the scheduler and live tools refuse to run against them.
PLACEHOLDER_TOKENS = frozenset({"", "placeholder-bearer-token", "your-x-api-bearer-token"})

#: Placeholder model names treated as "not configured" (no LLM health probe).
PLACEHOLDER_MODEL_NAMES = frozenset({"", "placeholder-model", "your-model-name"})


class Settings(BaseSettings):
    """All environment configuration for the Baleen backend.

    Values are read from environment variables (or the repo-root ``.env``),
    matching ``.env.example``. Secrets are never hardcoded here. Invalid values
    fail fast at startup instead of surfacing as confusing runtime errors.
    """

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # --- X (Twitter) API v2 ---
    x_api_bearer_token: str = "placeholder-bearer-token"
    x_user_handles: str = "unusual_whales,congress_trades"
    x_search_window_seconds: int = Field(default=3600, ge=1, le=7 * 24 * 3600)
    x_max_pages: int = Field(default=3, ge=1, le=10)
    x_api_timeout_seconds: int = Field(default=30, ge=1, le=600)
    poll_interval_seconds: int = Field(default=300, ge=5)

    # --- LLM (vLLM, OpenAI-compatible) ---
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "placeholder-key"
    llm_model_name: str = "placeholder-model"
    llm_tool_mode: Literal["auto", "tools", "json"] = "auto"
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_timeout_seconds: int = Field(default=90, ge=5, le=600)
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "baleen"
    postgres_user: str = "baleen"
    postgres_password: str = "baleen"
    database_url: str = ""

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "baleen_dev_pass"
    neo4j_version: int = 5

    # Read-only role used by the query agent (see docs/SETUP.md).
    neo4j_read_uri: str = ""
    neo4j_read_user: str = "baleen_reader"
    neo4j_read_password: str = "baleen_reader"

    # Cap on any single Cypher statement (query agent, graph export, etc.) so a
    # pathological query can't hang the API. 0 disables the cap.
    neo4j_query_timeout_seconds: int = Field(default=30, ge=0, le=600)

    # --- Scheduler ---
    scheduler_enabled: bool = True
    seed_default_alerts: bool = True
    # Tweets pulled per processing pass (extract -> graph -> alert). A live
    # 3-page poll can return 300 tweets; batch them so one run drains the queue.
    process_batch_size: int = Field(default=100, ge=1, le=1000)

    @field_validator("llm_base_url")
    @classmethod
    def _trim_llm_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("llm_base_url")
    @classmethod
    def _require_http_scheme(cls, value: str) -> str:
        if urlparse(value).scheme not in ("http", "https"):
            raise ValueError(
                f"llm_base_url must be an http(s) URL (got {value!r}; "
                "e.g. http://localhost:8001/v1)"
            )
        return value

    @field_validator("neo4j_uri", "neo4j_read_uri")
    @classmethod
    def _require_bolt_scheme(cls, value: str) -> str:
        if not value:
            return value  # read URI is optional
        if urlparse(value).scheme not in ("bolt", "bolt+s", "neo4j", "neo4j+s"):
            raise ValueError(
                f"Neo4j URIs must be bolt(s):// or neo4j(s):// (got {value!r})"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _require_postgres_scheme(cls, value: str) -> str:
        if not value:
            return value  # optional; derived from *_host fields otherwise
        if not urlparse(value).scheme.startswith(("postgresql", "postgres")):
            raise ValueError(
                f"database_url must be postgresql:// (got {value!r})"
            )
        return value

    @property
    def postgres_dsn(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def x_handles(self) -> list[str]:
        return [h.strip() for h in self.x_user_handles.split(",") if h.strip()]

    @property
    def x_api_configured(self) -> bool:
        """True only when a real X API bearer token is supplied."""
        return self.x_api_bearer_token not in PLACEHOLDER_TOKENS

    @property
    def llm_configured(self) -> bool:
        """True when a real model name is configured (not a placeholder).

        Used to decide whether /health should probe the LLM endpoint — probing
        a nonexistent dev endpoint every 15s would be noise.
        """
        return self.llm_model_name not in PLACEHOLDER_MODEL_NAMES


@lru_cache
def get_settings() -> Settings:
    return Settings()
