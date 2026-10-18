"""Settings, read from environment variables (prefix FMS_) or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FMS_", env_file=".env", extra="ignore")

    # Content.
    corpus_path: Path = PROJECT_DIR / "data" / "corpus.json"
    council_name: str = "Demo City Council"
    contact_url: str = "https://localgov-council.ddev.site/about-website/accessibility-statement"
    contact_phone: str = "0300 123 4567"

    # Answering.
    provider: str = Field(default="auto", pattern="^(auto|anthropic|extractive)$")
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    model: str = "claude-haiku-5-5"
    max_output_tokens: int = 500
    request_timeout_seconds: float = 20.0
    top_k: int = 4
    # Below this retrieval score the assistant says it does not know.
    min_score: float = 2.0
    # The best section must contain two of the question's words, or half of them.
    # This stops a single shared word ("night", "last") counting as a match.
    min_coverage: float = 0.5

    # Cost control. Prices are US dollars per million tokens.
    price_input_per_mtok: float = 0.10
    price_output_per_mtok: float = 0.50
    daily_budget_usd: float = 2.00

    # Abuse protection.
    rate_limit_per_minute: int = 10
    max_question_chars: int = 500


@lru_cache
def get_settings() -> Settings:
    return Settings()
