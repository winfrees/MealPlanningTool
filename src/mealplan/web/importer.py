"""Import the core collection from the browser (UI-8, ING-1).

The same `import_manifest` as `mealctl import pdf`, run one recipe at a time on a background
thread so the page can show progress and other requests keep working. Each recipe commits on
its own, so stopping part way keeps what was imported. Web prints are parsed for free; the
other pages need the Claude extractor, used only when an API key is configured.
"""

import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pymupdf
from sqlalchemy import Engine

from mealplan import db
from mealplan.agents.extractor import RecipeExtractor
from mealplan.core.normalizer import Catalog
from mealplan.ingest.pdf import BudgetExceeded, import_manifest
from mealplan.models.manifest import Manifest


class ImportRefused(ValueError):
    """The file is not the collection's PDF, or an import is already running."""


def check_pdf(path: Path, manifest: Manifest) -> None:
    """The manifest's page numbers only make sense for the collection's own PDF."""
    try:
        with pymupdf.open(path) as doc:
            pages = doc.page_count
    except Exception as e:  # pymupdf raises several types for a bad file
        raise ImportRefused(f"{path.name} is not a readable PDF") from e
    if pages != manifest.pages:
        raise ImportRefused(
            f"{path.name} has {pages} pages; the recipe collection "
            f"({manifest.source_pdf}) has {manifest.pages}"
        )


@dataclass
class ImportStatus:
    state: str = "idle"  # idle | running | done | stopped
    done: int = 0
    total: int = 0
    created: int = 0
    skipped: int = 0
    needs_agent: int = 0
    failed: int = 0
    message: str = ""
    uses_agent: bool = False


class ImportJob:
    def __init__(
        self,
        engine: Engine,
        manifest: Manifest,
        extractor: Callable[[], RecipeExtractor | None],
        weekly_budget_usd: float,
    ) -> None:
        self.engine = engine
        self.manifest = manifest
        self.extractor = extractor
        self.budget = weekly_budget_usd
        self._lock = threading.Lock()
        self._status = ImportStatus()
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return asdict(self._status)

    def running(self) -> bool:
        with self._lock:
            return self._status.state == "running"

    def start(self, pdf: Path) -> None:
        check_pdf(pdf, self.manifest)
        with self._lock:
            if self._status.state == "running":
                raise ImportRefused("an import is already running")
            extractor = self.extractor()
            self._status = ImportStatus(
                state="running", total=len(self.manifest.recipes), uses_agent=extractor is not None
            )
        self._thread = threading.Thread(target=self._run, args=(pdf, extractor), daemon=True)
        self._thread.start()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _update(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self._status, key, value)

    def _run(self, pdf: Path, extractor: RecipeExtractor | None) -> None:
        s = self._status
        try:
            with db.session_scope(self.engine) as session:
                catalog = Catalog.from_db(session)
            for entry in self.manifest.recipes:
                with db.session_scope(self.engine) as session:
                    report = import_manifest(
                        session,
                        pdf,
                        self.manifest,
                        catalog,
                        extractor,
                        only={entry.id},
                        weekly_budget_usd=self.budget,
                    )
                self._update(
                    done=s.done + 1,
                    created=s.created + len(report.created),
                    skipped=s.skipped + len(report.skipped),
                    needs_agent=s.needs_agent + len(report.needs_agent),
                    failed=s.failed + len(report.failed),
                )
        except BudgetExceeded as e:
            self._update(state="stopped", message=f"{e}. Recipes imported so far are kept.")
            return
        except Exception as e:  # keep the server up; show the reason on the page
            self._update(
                state="stopped", message=f"Import stopped: {e}. Recipes imported so far are kept."
            )
            return
        self._update(state="done")
