"""Which extractor reads the pages the free web-print parser can't (ING-1): Claude through
the API, a local model through Ollama, or none. Shared by the CLI and the web app."""

from collections.abc import Callable
from typing import Literal

import anthropic

from mealplan.agents.extractor import ClaudeExtractor, RecipeExtractor
from mealplan.agents.local_extractor import OllamaExtractor, check_local
from mealplan.config import Settings

Engine = Literal["claude", "local"]
ENGINE_CHOICES = ("auto", "claude", "local", "none")


def pick_engine(choice: str, has_key: bool, local_ready: Callable[[], bool]) -> Engine | None:
    """`auto` prefers Claude when a key is set, then a local model that is ready."""
    if choice == "claude":
        return "claude" if has_key else None
    if choice == "local":
        return "local"
    if choice == "none":
        return None
    if has_key:
        return "claude"
    return "local" if local_ready() else None


def make_extractor(
    engine: Engine | None, settings: Settings, key: str | None
) -> RecipeExtractor | None:
    if engine == "claude" and key:
        return ClaudeExtractor(anthropic.Anthropic(api_key=key))
    if engine == "local":
        return OllamaExtractor(
            model=settings.ollama_model, url=settings.ollama_url, vision=settings.ollama_vision
        )
    return None


def local_ready(settings: Settings) -> bool:
    return check_local(settings.ollama_model, settings.ollama_url).ready
