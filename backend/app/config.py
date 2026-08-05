from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment configuration for the Baleen backend.

    Values are read from environment variables (or a local ``.env`` file),
    matching ``.env.example`` at the repository root. Secrets are never
    hardcoded here.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- X (Twitter) API v2 ---
    x_api_bearer_token: str = "placeholder-bearer-token"
    x_user_handles: str = "unusual_whales,congress_trades"
    x_search_window_seconds: int = 3600
    poll_interval_seconds: int = 300

    # --- LLM (vLLM, OpenAI-compatible) ---
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "placeholder-key"
    llm_model_name: str = "placeholder-model"
    llm_tool_mode: str = "auto"
    llm_max_retries: int = 2
    confidence_threshold: float = 0.6

    # --- PostgreSQL ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "baleen"
    postgres_user: str = "baleen"
    postgres_password: str = "baleen"
    database_url: str = ""

    # --- Neo4j ---
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "baleen"
    neo4j_version: int = 5

    # Read-only role used by the query agent (see docs/SETUP.md).
    neo4j_read_uri: str = ""
    neo4j_read_user: str = "baleen_reader"
    neo4j_read_password: str = "baleen_reader"

    # --- Scheduler ---
    scheduler_enabled: bool = True

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
