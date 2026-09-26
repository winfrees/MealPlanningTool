"""Runtime settings, read from the environment and an optional `.env` file (NFR-6)."""

from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEALPLAN_", env_file=".env", extra="ignore")

    db_path: Path = Path("mealplan.db")
    data_dir: Path = Path("data")
    anthropic_api_key: SecretStr | None = None
    agent_weekly_budget_usd: float = 10.0

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


def get_settings() -> Settings:
    return Settings()
