"""The web app: JSON API over the core plus the static frontend (UI-4..UI-7, ADR-0007).

Handlers are thin: each calls the same core function as the matching CLI command.
"""

from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mealplan import db
from mealplan.config import Settings
from mealplan.core import inventory, kitchen, library, plan_store, recipe_facts
from mealplan.core.base_week import rotation
from mealplan.core.inventory import InventoryError, Shortfall
from mealplan.core.library import LibraryError
from mealplan.core.normalizer import Catalog
from mealplan.core.plan_store import PlanError
from mealplan.core.planner import PlannedMeal, WeekPlanResult
from mealplan.core.preferences import PrefsError, Weekday, load_prefs, set_pref
from mealplan.core.prep import PrepPlan, build_prep
from mealplan.core.render import day_card
from mealplan.core.render_list import (
    line_text,
    shopping_markdown,
    shopping_pdf_bytes,
    shopping_text,
)
from mealplan.core.scaling import scale_quantity
from mealplan.core.shopping import ListLine
from mealplan.core.units import format_qty
from mealplan.ingest import review_queue
from mealplan.models.enums import Location, Meal, RecipeStatus
from mealplan.models.tables import Ingredient, Rating, Recipe
from mealplan.web import auth

STATIC = Path(__file__).resolve().parent / "static"
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# --- request bodies ---------------------------------------------------------------------------


class LoginBody(BaseModel):
    password: str


class PlanBody(BaseModel):
    start: date | None = None
    seed: int | None = None
    force: bool = False


class SwapBody(BaseModel):
    start: date | None = None
    day: date
    meal: Meal
    ref: str


class LockBody(BaseModel):
    start: date | None = None
    locked: bool


class CookedBody(BaseModel):
    day: date
    meal: Meal = Meal.DINNER


class StartBody(BaseModel):
    start: date | None = None


class RateBody(BaseModel):
    score: int = Field(ge=1, le=5)
    repeat: bool | None = None
    notes: str = ""


class TagsBody(BaseModel):
    add: list[str] = Field(default_factory=list)
    remove: list[str] = Field(default_factory=list)


class MergeBody(BaseModel):
    into: str


class FamilyBody(BaseModel):
    name: str = Field(min_length=1)


class InventoryBody(BaseModel):
    name: str
    qty: float = Field(gt=0)
    unit: str | None = None
    location: Location = Location.FRIDGE
    best_by: date | None = None


class QtyBody(BaseModel):
    qty: float = Field(ge=0)


class StaplesBody(BaseModel):
    out: list[str] = Field(default_factory=list)


class PrefBody(BaseModel):
    key: str
    value: str


# --- serialization ----------------------------------------------------------------------------


def _meal(m: PlannedMeal | None) -> dict[str, Any] | None:
    if m is None:
        return None
    return {
        "date": m.date.isoformat(),
        "meal": m.meal.value,
        "ref": m.ref,
        "title": m.title,
        "servings": m.servings,
        "leftover_of": m.leftover_of.isoformat() if m.leftover_of else None,
        "components": list(m.components),
        "locked": m.locked,
    }


def _week(
    result: WeekPlanResult, status: str, prep: PrepPlan, dishes: dict[str, Any]
) -> dict[str, Any]:
    meals = {(m.date, m.meal): m for m in result.meals}
    days = sorted({m.date for m in result.meals})
    return {
        "week_start": result.week_start.isoformat(),
        "seed": result.seed,
        "status": status,
        "conflicts": list(result.conflicts),
        "days": [
            {
                "date": d.isoformat(),
                "label": d.strftime("%a %d %b"),
                "lunch": _meal(meals.get((d, Meal.LUNCH))),
                "dinner": _meal(meals.get((d, Meal.DINNER))),
                "card": day_card(d, result, prep, dishes)[1:],
            }
            for d in days
        ],
        "prep": {
            "day": prep.day.isoformat(),
            "est_minutes": prep.est_minutes,
            "tasks": [
                {
                    "start": s.start,
                    "name": s.task.name,
                    "active": s.task.active,
                    "passive": s.task.passive,
                    "equipment": list(s.task.equipment),
                    "note": s.task.note,
                }
                for s in prep.tasks
            ],
            "storage": list(prep.storage),
            "conflicts": list(prep.conflicts),
        },
    }


def _line(line: ListLine) -> dict[str, Any]:
    return {
        "name": line.name,
        "section": line.section,
        "unit": line.unit,
        "needed": line.needed,
        "on_hand": line.on_hand,
        "to_buy": line.to_buy,
        "packs": line.packs,
        "notes": list(line.notes),
        "uses": list(line.uses),
        "text": line_text(line),
    }


def _shortfalls(short: list[Shortfall]) -> list[str]:
    return [f"{s.name} ({format_qty(s.qty)} {s.unit})" for s in short]


# --- the app ----------------------------------------------------------------------------------


def create_app(settings: Settings, today: Callable[[], date] = date.today) -> FastAPI:
    if settings.web_password is None or not settings.web_password.get_secret_value():
        raise RuntimeError("set MEALPLAN_WEB_PASSWORD before starting the web app")
    password = settings.web_password.get_secret_value()
    db.upgrade(settings.db_url)
    engine = db.make_engine(settings.db_url)
    secret = auth.load_secret(settings.web_secret_path)
    limiter = auth.LoginLimiter()

    app = FastAPI(title="Meal planner", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Any]) -> Response:
        path = request.url.path
        if path.startswith("/api/"):
            if request.method in UNSAFE_METHODS and request.headers.get(auth.CSRF_HEADER) != "1":
                response: Response = JSONResponse({"detail": "missing request header"}, 403)
            elif path not in ("/api/login", "/api/session") and not auth.token_ok(
                secret, request.cookies.get(auth.COOKIE)
            ):
                response = JSONResponse({"detail": "log in first"}, 401)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    def session() -> Iterator[Session]:
        with db.session_scope(engine) as s:
            yield s

    S = Annotated[Session, Depends(session)]

    for error, status in (
        (LibraryError, 400),
        (PlanError, 400),
        (PrefsError, 400),
        (InventoryError, 400),
    ):

        def handler(_request: Request, exc: Exception, status: int = status) -> JSONResponse:
            return JSONResponse({"detail": str(exc)}, status)

        app.add_exception_handler(error, handler)

    def week_start(s: Session, start: date | None) -> date:
        return start or plan_store.next_prep_day(today(), load_prefs(s))

    def week_json(s: Session, start: date) -> dict[str, Any]:
        result = plan_store.saved_plan(s, start)
        week = plan_store.get_week(s, start)
        dishes = plan_store.plan_dishes(s, result)
        return _week(result, week.status if week else "draft", build_prep(result, dishes), dishes)

    # --- session ---

    @app.get("/api/session")
    def get_session(request: Request) -> dict[str, bool]:
        return {"authenticated": auth.token_ok(secret, request.cookies.get(auth.COOKIE))}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
        client = request.client.host if request.client else "unknown"
        if limiter.blocked(client):
            raise HTTPException(429, "too many attempts; try again in a few minutes")
        if not auth.password_ok(body.password, password):
            limiter.failed(client)
            raise HTTPException(401, "wrong password")
        limiter.succeeded(client)
        response.set_cookie(
            auth.COOKIE,
            auth.make_token(secret),
            max_age=auth.SESSION_SECONDS,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(response: Response) -> dict[str, bool]:
        response.delete_cookie(auth.COOKIE)
        return {"authenticated": False}

    # --- week plan ---

    @app.get("/api/week")
    def get_week(s: S, start: date | None = None) -> dict[str, Any]:
        begin = week_start(s, start)
        if plan_store.get_week(s, begin) is None:
            raise HTTPException(404, f"no plan for the week of {begin}")
        return week_json(s, begin)

    @app.post("/api/week/plan")
    def plan(body: PlanBody, s: S) -> dict[str, Any]:
        begin = week_start(s, body.start)
        plan_store.plan_and_save(s, begin, load_prefs(s), seed=body.seed, force=body.force)
        return week_json(s, begin)

    @app.post("/api/week/swap")
    def swap(body: SwapBody, s: S) -> dict[str, Any]:
        begin = week_start(s, body.start)
        plan_store.swap(s, begin, body.day, body.meal, body.ref, load_prefs(s))
        return week_json(s, begin)

    @app.post("/api/week/lock")
    def lock(body: LockBody, s: S) -> dict[str, Any]:
        begin = week_start(s, body.start)
        plan_store.set_locked(s, begin, body.locked)
        return week_json(s, begin)

    @app.post("/api/week/cooked")
    def cooked(body: CookedBody, s: S) -> dict[str, Any]:
        return {"not_in_inventory": _shortfalls(kitchen.cook(s, body.day, body.meal))}

    @app.post("/api/prep/done")
    def prep_done(body: StartBody, s: S) -> dict[str, Any]:
        begin = week_start(s, body.start)
        return {"not_in_inventory": _shortfalls(kitchen.prep_done(s, begin))}

    @app.get("/api/base-week")
    def base_week(s: S) -> list[dict[str, Any]]:
        prefs = load_prefs(s)
        titles = {r.ref: r.title for r in s.scalars(select(Recipe))}
        return [
            {
                "day": d.value,
                "meals": [
                    {"ref": r, "title": titles.get(r, r)}
                    for r in rotation(prefs.base_week.get(d, ""))
                ],
            }
            for d in Weekday
        ]

    # --- recipes ---

    @app.get("/api/recipes")
    def recipes(
        s: S,
        q: str = "",
        role: str | None = None,
        tag: str | None = None,
        family: str | None = None,
        min_rating: float | None = None,
        status: RecipeStatus = RecipeStatus.APPROVED,
    ) -> list[dict[str, Any]]:
        averages = select(Rating.recipe_id, func.avg(Rating.score)).group_by(Rating.recipe_id)
        ratings = {rid: avg for rid, avg in s.execute(averages)}
        needle = q.strip().lower()
        out = []
        for r in s.scalars(select(Recipe).where(Recipe.status == status).order_by(Recipe.title)):
            dish = plan_store.dish_from_recipe(s, r)
            if role and (r.meal_role is None or r.meal_role.value != role):
                continue
            if tag and tag not in r.tags:
                continue
            if family and (r.family is None or r.family.name != family):
                continue
            rating = ratings.get(r.id)
            if min_rating is not None and (rating is None or rating < min_rating):
                continue
            haystack = " ".join([r.title, *(i.name for i in dish.ingredients)]).lower()
            if needle and needle not in haystack:
                continue
            out.append(
                {
                    "ref": r.ref,
                    "title": r.title,
                    "role": r.meal_role.value if r.meal_role else None,
                    "tags": r.tags,
                    "family": r.family.name if r.family else None,
                    "collection": r.collection.value,
                    "rating": round(float(rating), 2) if rating is not None else None,
                    "active_minutes": recipe_facts.active_minutes(dish),
                    "protein": recipe_facts.protein(dish),
                }
            )
        return out

    @app.get("/api/recipes/{ref}")
    def recipe(
        ref: str, s: S, servings: Annotated[float | None, Query(gt=0)] = None
    ) -> dict[str, Any]:
        r = library.get_recipe(s, ref)
        factor = servings / r.servings if servings and r.servings else 1.0
        ingredients = []
        for ing in r.ingredients:
            name = ing.ingredient.canonical_name if ing.ingredient else None
            if ing.qty is None or factor == 1.0:
                qty, unit = ing.qty, ing.unit
            else:
                qty, unit = scale_quantity(ing.qty, ing.unit, factor, name or ing.raw_text)
            ingredients.append(
                {
                    "raw": ing.raw_text,
                    "name": name,
                    "qty": format_qty(qty) if qty is not None else None,
                    "unit": unit,
                    "prep_note": ing.prep_note,
                    "matched": name is not None,
                }
            )
        variants = [v.ref for v in r.family.recipes if v.ref != r.ref] if r.family else []
        ratings = s.scalars(select(Rating).where(Rating.recipe_id == r.id).order_by(Rating.date))
        return {
            "ref": r.ref,
            "title": r.title,
            "status": r.status.value,
            "collection": r.collection.value,
            "role": r.meal_role.value if r.meal_role else None,
            "servings": r.servings,
            "shown_servings": servings or r.servings,
            "scaled": factor != 1.0,
            "prep_minutes": r.prep_minutes,
            "cook_minutes": r.cook_minutes,
            "tags": r.tags,
            "family": r.family.name if r.family else None,
            "variants": variants,
            "notes": r.household_notes,
            "sources": [
                {"title": x.title, "url": x.url, "file": x.file, "pages": x.pages}
                for x in r.sources
            ],
            "ingredients": ingredients,
            "steps": [s_.text for s_ in r.steps],
            "ratings": [
                {"date": x.date.isoformat(), "score": x.score, "notes": x.notes} for x in ratings
            ],
        }

    @app.post("/api/recipes/{ref}/rate")
    def rate(ref: str, body: RateBody, s: S) -> dict[str, Any]:
        r = library.get_recipe(s, ref)
        library.rate(s, r, body.score, today(), would_repeat=body.repeat, notes=body.notes)
        return {"ref": r.ref, "collection": r.collection.value}

    @app.post("/api/recipes/{ref}/tags")
    def tags(ref: str, body: TagsBody, s: S) -> dict[str, Any]:
        r = library.get_recipe(s, ref)
        r.tags = sorted(
            (set(r.tags) | {t.strip() for t in body.add if t.strip()}) - set(body.remove)
        )
        return {"ref": r.ref, "tags": r.tags}

    # --- review queue ---

    @app.get("/api/review")
    def review(s: S) -> list[dict[str, Any]]:
        out = []
        for r in review_queue.pending(s):
            item = review_queue.review(s, r)
            out.append(
                {
                    "ref": r.ref,
                    "title": r.title,
                    "issues": item.issues,
                    "similar": [
                        {"ref": m.recipe.ref, "title": m.recipe.title, "score": m.score}
                        for m in item.similar
                    ],
                }
            )
        return out

    @app.post("/api/review/{ref}/approve")
    def approve(ref: str, s: S) -> dict[str, str]:
        review_queue.approve(s, library.get_recipe(s, ref))
        return {"ref": ref, "status": "approved"}

    @app.post("/api/review/{ref}/reject")
    def reject(ref: str, s: S) -> dict[str, str]:
        review_queue.reject(s, library.get_recipe(s, ref))
        return {"ref": ref, "status": "rejected"}

    @app.post("/api/review/{ref}/merge")
    def merge(ref: str, body: MergeBody, s: S) -> dict[str, Any]:
        target = library.merge_copy(s, library.get_recipe(s, ref), library.get_recipe(s, body.into))
        return {"ref": target.ref, "sources": len(target.sources)}

    @app.post("/api/review/{ref}/family")
    def family(ref: str, body: FamilyBody, s: S) -> dict[str, str]:
        library.add_to_family(s, library.get_recipe(s, ref), body.name.strip())
        return {"ref": ref, "family": body.name.strip()}

    # --- shopping list ---

    @app.get("/api/list")
    def shopping(s: S, start: date | None = None) -> dict[str, Any]:
        result = kitchen.shopping_list(s, week_start(s, start), today())
        return {
            "week_start": result.week_start.isoformat(),
            "lines": [_line(ln) for ln in result.lines],
            "have": [_line(ln) for ln in result.have],
            "staples": [ln.name for ln in result.staples],
            "checks": list(result.checks),
        }

    @app.get("/api/list/export")
    def export(s: S, start: date | None = None, format: str = "text") -> Response:
        result = kitchen.shopping_list(s, week_start(s, start), today())
        stem = f"shopping-{result.week_start.isoformat()}"
        if format == "pdf":
            return Response(
                shopping_pdf_bytes(result),
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
            )
        if format not in ("text", "md"):
            raise HTTPException(400, "format must be text, md or pdf")
        body = shopping_text(result) if format == "text" else shopping_markdown(result)
        ext = "txt" if format == "text" else "md"
        return Response(
            body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'},
        )

    # --- inventory ---

    @app.get("/api/inventory")
    def get_inventory(s: S) -> dict[str, Any]:
        names = {i.id: i.canonical_name for i in s.scalars(select(Ingredient))}
        soon = {i.id for i in inventory.expiring(s, today())}
        status = inventory.staple_status(s, today())
        return {
            "items": [
                {
                    "id": i.id,
                    "name": names[i.ingredient_id],
                    "qty": i.qty,
                    "qty_text": format_qty(i.qty),
                    "unit": i.unit,
                    "location": i.location.value,
                    "best_by": i.best_by.isoformat() if i.best_by else None,
                    "expiring": i.id in soon,
                }
                for i in inventory.items(s)
            ],
            "staples": {
                "all": list(status.staples),
                "out": list(status.out),
                "last_checked": status.last_checked.isoformat() if status.last_checked else None,
                "check_due": status.check_due,
            },
        }

    @app.post("/api/inventory")
    def add_inventory(body: InventoryBody, s: S) -> dict[str, Any]:
        item = inventory.add_item(
            s,
            Catalog.from_db(s),
            body.name,
            body.qty,
            body.unit,
            body.location,
            today(),
            body.best_by,
        )
        return {"id": item.id}

    @app.patch("/api/inventory/{item_id}")
    def set_inventory(item_id: int, body: QtyBody, s: S) -> dict[str, int]:
        inventory.set_qty(s, item_id, body.qty)
        return {"id": item_id}

    @app.delete("/api/inventory/{item_id}")
    def delete_inventory(item_id: int, s: S) -> dict[str, int]:
        inventory.remove_item(s, item_id)
        return {"id": item_id}

    @app.post("/api/staples")
    def staples(body: StaplesBody, s: S) -> dict[str, Any]:
        status = inventory.check_staples(s, Catalog.from_db(s), today(), body.out)
        return {"out": list(status.out), "check_due": status.check_due}

    @app.get("/api/catalog")
    def catalog(s: S) -> list[str]:
        return sorted(s.scalars(select(Ingredient.canonical_name)))

    # --- preferences ---

    @app.get("/api/prefs")
    def prefs(s: S) -> dict[str, Any]:
        return load_prefs(s).model_dump(mode="json")

    @app.post("/api/prefs")
    def update_pref(body: PrefBody, s: S) -> dict[str, Any]:
        return set_pref(s, body.key, body.value).model_dump(mode="json")

    # --- frontend ---

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
