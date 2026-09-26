"""Deterministic parser for text web prints (the M1 "text layer" path, no agent).

Recipe-plugin print pages share a shape: title, times and servings, an "Ingredients"
heading, then "Instructions". When that shape is missing this returns None and the page
goes to the extraction agent instead.
"""

import re

from mealplan.agents.extractor import ExtractedRecipe, ExtractedStep

_INGREDIENTS_RE = re.compile(r"^ingredients\b[\s:]*$", re.IGNORECASE)
_STEPS_RE = re.compile(
    r"^(instructions|directions|method|preparation|steps)\b[\s:]*$", re.IGNORECASE
)
_STOP_RE = re.compile(
    r"^(notes?|nutrition|video|equipment|course|cuisine|keyword|author|tried this|"
    r"did you make|recipe notes|nutrition facts)\b",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^[▢•●▪*]\s*")
_NUMBERED_RE = re.compile(r"^(?:step\s*)?\d+[.):]\s+", re.IGNORECASE)
_NOISE_RE = re.compile(r"^(us customary|metric|\d+x(\s*\d+x)*|cups?\s*/\s*grams?)$", re.IGNORECASE)
_SERVINGS_RE = re.compile(
    r"\b(?:servings|serves|yield|makes)\b\s*:?\s*(\d+(?:\.\d+)?)", re.IGNORECASE
)
_DURATION_PART = re.compile(r"(\d+)\s*(h|hr|hrs|hours?|m|min|mins|minutes?)\b", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """ "1 hr 20 mins" -> 80."""
    total = 0
    found = False
    for n, unit in _DURATION_PART.findall(text):
        found = True
        total += int(n) * (60 if unit.lower().startswith("h") else 1)
    return total if found else None


def _time(label: str, text: str) -> int | None:
    m = re.search(rf"\b{label}\s*(?:time)?\s*:?\s*((?:\d+\s*[a-z]+\s*){{1,2}})", text, re.I)
    return parse_duration(m[1]) if m else None


def _is_subheading(line: str) -> bool:
    return line.endswith(":") or line.lower().startswith("for the ")


def _ingredient_lines(lines: list[str]) -> list[str]:
    lines = [ln for ln in lines if not _NOISE_RE.match(ln) and not _is_subheading(ln)]
    bulleted = any(_BULLET_RE.match(ln) for ln in lines)
    out: list[str] = []
    for ln in lines:
        if bulleted:
            if _BULLET_RE.match(ln) or not out:
                out.append(_BULLET_RE.sub("", ln))
            else:
                out[-1] = f"{out[-1]} {ln}"  # wrapped line
        else:
            out.append(ln)
    return [ln.strip() for ln in out if ln.strip()]


def _steps(lines: list[str]) -> list[ExtractedStep]:
    numbered = any(_NUMBERED_RE.match(ln) or _BULLET_RE.match(ln) for ln in lines)
    texts: list[str] = []
    for ln in lines:
        starts_new = bool(_NUMBERED_RE.match(ln) or _BULLET_RE.match(ln))
        clean = _BULLET_RE.sub("", _NUMBERED_RE.sub("", ln)).strip()
        if not clean:
            continue
        if not texts or (starts_new if numbered else texts[-1].endswith((".", "!", ")"))):
            texts.append(clean)
        else:
            texts[-1] = f"{texts[-1]} {clean}"
    return [
        ExtractedStep(text=t, equipment=[], active_minutes=None, passive_minutes=None)
        for t in texts
    ]


def parse_web_print(text: str, title: str, pages: list[int]) -> ExtractedRecipe | None:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ing_at = next((i for i, ln in enumerate(lines) if _INGREDIENTS_RE.match(ln)), None)
    if ing_at is None:
        return None
    steps_at = next((i for i in range(ing_at + 1, len(lines)) if _STEPS_RE.match(lines[i])), None)
    if steps_at is None:
        return None
    stop_at = next(
        (i for i in range(steps_at + 1, len(lines)) if _STOP_RE.match(lines[i])), len(lines)
    )
    ingredients = _ingredient_lines(lines[ing_at + 1 : steps_at])
    steps = _steps(lines[steps_at + 1 : stop_at])
    if len(ingredients) < 2 or not steps:
        return None

    header = "\n".join(lines[:ing_at])
    servings = _SERVINGS_RE.search(header)
    return ExtractedRecipe(
        title=title,
        servings=float(servings[1]) if servings else None,
        prep_minutes=_time("prep", header),
        cook_minutes=_time("cook", header),
        total_minutes=_time("total", header),
        ingredients=ingredients,
        steps=steps,
        pages=pages,
    )
