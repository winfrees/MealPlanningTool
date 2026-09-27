"""Runtime settings, read from the environment and an optional `.env` file (NFR-6)."""

from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEALPLAN_", env_file=".env", extra="ignore")

    db_path: Path = Path("mealplan.db")
    data_dir: Path = Path("data")
    # Either name works, in the environment or in `.env`.
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MEALPLAN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"),
    )
    agent_weekly_budget_usd: float = 10.0
    # Who reads scanned pages: auto = Claude when a key is set, else a local model when
    # Ollama is running; claude, local, or none to choose.
    extractor: Literal["auto", "claude", "local", "none"] = "auto"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_vision: bool = False  # also send page images (for a vision model)
    web_password: SecretStr | None = None  # required by `mealctl serve` (UI-6)
    web_secret_file: Path | None = None  # session signing secret; default: beside the DB

    @property
    def web_secret_path(self) -> Path:
        return self.web_secret_file or self.db_path.with_suffix(".secret")

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_settings() -> Settings:
    return Settings()
