"""The Anthropic API key: tidy what a person pastes, and check it works (UI-8, NFR-6).

The check asks the Models API about the extractor's model, which costs nothing and fails the
same way a real call would (bad key, no access, no network).
"""

import anthropic

from mealplan.agents.extractor import DEFAULT_MODEL

KEY_PREFIX = "sk-ant-"
NAMES = ("MEALPLAN_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


class KeyProblem(ValueError):
    """Why a key cannot be used, in words for the household."""


def clean_key(raw: str) -> str:
    """Accept a key pasted with its variable name, quotes or spaces around it."""
    key = raw.strip()
    for name in NAMES:
        if key.startswith(name):
            key = key[len(name) :].lstrip(" =:")
            break
    key = key.strip().strip("\"'").strip()
    if not key:
        raise KeyProblem("paste your Anthropic API key")
    if not key.startswith(KEY_PREFIX) or any(c.isspace() for c in key):
        raise KeyProblem(
            f"that doesn't look like an Anthropic API key: keys start with {KEY_PREFIX} and "
            "come from console.anthropic.com (Settings, API keys). The model name isn't needed."
        )
    return key


def check_key(client: anthropic.Anthropic, model: str = DEFAULT_MODEL) -> None:
    """Raise KeyProblem unless the key can reach the model the extractor uses."""
    try:
        client.models.retrieve(model)
    except anthropic.AuthenticationError:
        raise KeyProblem(
            "Anthropic rejected that key; check it was copied in full, "
            "or create a new one at console.anthropic.com"
        ) from None
    except anthropic.PermissionDeniedError:
        raise KeyProblem(f"that key's account cannot use {model}") from None
    except anthropic.NotFoundError:
        raise KeyProblem(f"{model} is not available to that key's account") from None
    except anthropic.APIConnectionError:
        raise KeyProblem("could not reach api.anthropic.com; check the connection") from None
    except anthropic.APIStatusError as e:
        raise KeyProblem(f"Anthropic API error ({e.status_code}): {e.message}") from None
