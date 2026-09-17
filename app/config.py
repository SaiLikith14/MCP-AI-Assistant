from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Also push .env into os.environ so ${VAR} placeholders in mcp_config.json can be
# expanded. pydantic-settings alone only populates Settings, not the environment.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Gemini
    gemini_api_key: str
    # Check https://ai.google.dev/gemini-api/docs/models for the current
    # lineup (Pro/Flash/Flash-Lite) and pick the cost/quality tradeoff you want.
    model: str = "gemini-3.5-flash-lite"
    # Gemini's internal "thinking" tokens count against this budget, so keep it
    # well above the length of the answer you actually expect.
    max_tokens: int = 8192

    # Auth between your frontend and this backend (shared secret).
    # Call this API only from your frontend's server/edge layer, never from
    # client-side JS, or anyone can read the key out of the browser.
    backend_api_key: str

    # CORS: comma-separated list of origins allowed to call this API directly
    # (only needed if the UI calls this backend straight from the browser).
    allowed_origins: str = ""

    # MCP
    mcp_config_path: Path = Path("mcp_config.json")

    # Database. Defaults to a local SQLite file so nothing extra is needed to run
    # locally; Render supplies a Postgres URL via this same variable.
    database_url: str = "sqlite+aiosqlite:///./agent.db"

    # Server
    port: int = 8000

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        # Render hands out "postgres://..." / "postgresql://...", but SQLAlchemy's
        # async engine needs an async driver spelled out explicitly.
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg://", 1)
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
