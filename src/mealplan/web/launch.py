"""Starting the web app with one command (UI-8): first-run password and the demo household."""

import os
from datetime import date
from pathlib import Path

from pydantic import SecretStr

from mealplan import db
from mealplan.config import Settings
from mealplan.core import setup

ENV_KEY = "MEALPLAN_WEB_PASSWORD"
DEMO_PASSWORD = "demo"
MIN_PASSWORD = 8


def save_password(env_file: Path, password: str) -> None:
    """Add the household password to `.env` (git-ignored), readable by this user only."""
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"use at least {MIN_PASSWORD} characters")
    save_env_value(env_file, ENV_KEY, password)


def save_env_value(env_file: Path, name: str, value: str, replaces: tuple[str, ...] = ()) -> None:
    """Set `name=value` in `.env`, dropping earlier lines for it (and for `replaces`), and keep
    the file readable by this user only."""
    if "\n" in value or "\r" in value:
        raise ValueError("the value must be on one line")
    names = (name, *replaces)
    lines = []
    if env_file.exists():
        lines = [
            line
            for line in env_file.read_text(encoding="utf-8").splitlines()
            if line.split("=", 1)[0].strip() not in names
        ]
    lines.append(f"{name}={value}")
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    env_file.chmod(0o600)


def demo_settings(settings: Settings, today: date) -> Settings:
    """A fresh sample household in `demo.db` beside the real database, which stays untouched."""
    path = settings.db_path.with_name("demo.db")
    path.unlink(missing_ok=True)
    demo = settings.model_copy(
        update={"db_path": path, "web_password": SecretStr(DEMO_PASSWORD), "web_secret_file": None}
    )
    db.upgrade(demo.db_url)
    with db.session_scope(db.make_engine(demo.db_url)) as s:
        setup.populate_demo(s, demo.data_dir, today)
    return demo
