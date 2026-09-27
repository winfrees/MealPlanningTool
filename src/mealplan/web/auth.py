"""Household password and sessions for the web app (UI-6, ADR-0007).

A session is a signed, expiring token in an HttpOnly, SameSite=Strict cookie:
`<expiry>.<hmac-sha256(secret, expiry)>`. The secret is generated once per install and kept
beside the database with owner-only permissions.
"""

import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

COOKIE = "mealplan_session"
CSRF_HEADER = "X-Mealplan"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 600


def load_secret(path: Path) -> bytes:
    """The install's signing secret, created on first use (owner read/write only)."""
    if path.exists():
        return bytes.fromhex(path.read_text(encoding="ascii").strip())
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(secret.hex())
    return secret


def password_ok(given: str, expected: str) -> bool:
    return hmac.compare_digest(given.encode(), expected.encode())


def _sign(secret: bytes, payload: str) -> str:
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def make_token(secret: bytes, now: float | None = None, lifetime: int = SESSION_SECONDS) -> str:
    expires = int((now if now is not None else time.time()) + lifetime)
    return f"{expires}.{_sign(secret, str(expires))}"


def token_ok(secret: bytes, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, signature = token.split(".", 1)
    if not expires.isdigit() or not hmac.compare_digest(signature, _sign(secret, expires)):
        return False
    return int(expires) > (now if now is not None else time.time())


class LoginLimiter:
    """At most MAX_FAILURES failed logins per client within the window."""

    def __init__(self, max_failures: int = MAX_FAILURES, window: int = FAILURE_WINDOW_SECONDS):
        self.max_failures = max_failures
        self.window = window
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def _recent(self, client: str, now: float) -> deque[float]:
        times = self.failures[client]
        while times and times[0] <= now - self.window:
            times.popleft()
        return times

    def blocked(self, client: str, now: float | None = None) -> bool:
        return len(self._recent(client, now or time.time())) >= self.max_failures

    def failed(self, client: str, now: float | None = None) -> None:
        now = now or time.time()
        self._recent(client, now).append(now)

    def succeeded(self, client: str) -> None:
        self.failures.pop(client, None)
