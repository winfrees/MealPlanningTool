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
