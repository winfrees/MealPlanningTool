"""Run the scout from the browser (M5): one background run at a time, polled by the page.

A scout run takes a minute or two (web searches, then fetching and checking each page), so it
runs on a thread with its own database session; the page polls for the candidates.
"""

import threading
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine

from mealplan import db
from mealplan.agents.extractor import AccountError
from mealplan.agents.scout import RecipeScout
from mealplan.core import discovery
from mealplan.core.normalizer import Catalog
from mealplan.core.preferences import load_prefs
from mealplan.ingest.fetch import Fetcher
from mealplan.ingest.pdf import BudgetExceeded
from mealplan.models.enums import MealRole

CandidateJson = Callable[[discovery.Candidate], dict[str, Any]]


class ScoutJob:
    def __init__(
        self,
        engine: Engine,
        fetcher: Fetcher,
        to_json: CandidateJson,
        weekly_budget_usd: float,
    ) -> None:
        self.engine = engine
        self.fetcher = fetcher
        self.to_json = to_json
        self.budget = weekly_budget_usd
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {"state": "idle", "candidates": [], "message": ""}

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def start(self, scout: RecipeScout, request_text: str, role: MealRole | None) -> None:
        with self._lock:
            if self._status["state"] == "running":
                raise discovery.DiscoveryError("the scout is already looking")
            self._status = {"state": "running", "candidates": [], "message": "", "request": ""}
        threading.Thread(target=self._run, args=(scout, request_text, role), daemon=True).start()

    def _set(self, **changes: Any) -> None:
        with self._lock:
            self._status.update(changes)

    def _run(self, scout: RecipeScout, request_text: str, role: MealRole | None) -> None:
        try:
            with db.session_scope(self.engine) as s:
                found = discovery.scout_candidates(
                    s,
                    load_prefs(s),
                    Catalog.from_db(s),
                    scout,
                    request_text,
                    role,
                    self.fetcher,
                    self.budget,
                )
                self._set(state="done", candidates=[self.to_json(c) for c in found])
        except (AccountError, BudgetExceeded, discovery.DiscoveryError) as e:
            self._set(state="failed", message=str(e))
        except Exception as e:  # keep the server up; say what happened on the page
            self._set(state="failed", message=f"the scout stopped: {type(e).__name__}: {e}")
