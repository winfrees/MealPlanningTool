"""Shopping list exports (SHP-5): Markdown, plain text for phone notes, printable PDF."""

from pathlib import Path

import pymupdf

from mealplan.core.shopping import PACKAGE_UNITS, ListLine, ShoppingListResult
from mealplan.core.units import EACH, format_qty

_PLURALS = {
    "cup": "cups",
    "can": "cans",
    "jar": "jars",
    "package": "packages",
    "packet": "packets",
    "bag": "bags",
    "box": "boxes",
    "bottle": "bottles",
    "bunch": "bunches",
    "head": "heads",
    "stalk": "stalks",
    "sprig": "sprigs",
    "slice": "slices",
    "stick": "sticks",
    "piece": "pieces",
    "clove": "cloves",
    "fillet": "fillets",
    "leaf": "leaves",
    "pinch": "pinches",
    "dash": "dashes",
    "scoop": "scoops",
    "pint": "pints",
    "quart": "quarts",
    "gallon": "gallons",
}


def _amount(qty: float, unit: str) -> str:
    if unit == EACH:
        return format_qty(qty)
    shown = _PLURALS.get(unit, unit) if qty > 1 else unit
    return f"{format_qty(qty)} {shown}"


def line_text(line: ListLine) -> str:
    """One list line, e.g. `ground beef: 2 1/4 lb (3 x 1 lb) · need 3 1/4, have 1`."""
    if line.amount_known or line.to_buy > 0:
        text = f"{line.name}: {_amount(line.to_buy, line.unit)}"
        if not line.amount_known:
            text += " plus more"
    else:
        text = f"{line.name}: as needed"
    if line.packs is not None:
        if line.pack_size and line.unit not in PACKAGE_UNITS:
            text += f" ({line.packs} x {_amount(line.pack_size, line.pack_unit or EACH)})"
        elif line.pack_size:
            text += f" ({_amount(line.pack_size, line.pack_unit or EACH)} each)"
        elif line.unit not in PACKAGE_UNITS:
            text += f" ({line.packs} container)"
    if line.on_hand > 0:
        text += f" · need {format_qty(line.needed)}, have {format_qty(line.on_hand)}"
    if line.notes:
        text += f" · {'; '.join(line.notes)}"
    return text


def _sections(result: ShoppingListResult) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for line in result.lines:
        title = line.section.replace("-", " ").title()
        if not out or out[-1][0] != title:
            out.append((title, []))
        out[-1][1].append(line_text(line))
    return out


def shopping_markdown(result: ShoppingListResult) -> str:
    lines = [f"# Shopping: week of {result.week_start:%a %d %b %Y}"]
    for title, items in _sections(result):
        lines += ["", f"## {title}", *[f"- [ ] {item}" for item in items]]
    if result.have:
        lines += ["", "## Already have", *[f"- {line_text(ln)}" for ln in result.have]]
    if result.staples:
        names = ", ".join(s.name for s in result.staples)
        lines += ["", "## Staples (assumed on hand)", names]
    if result.checks:
        lines += ["", "## Check these", *[f"- {c}" for c in result.checks]]
    return "\n".join(lines) + "\n"


def shopping_text(result: ShoppingListResult) -> str:
    """Plain text for phone notes or reminders apps: no Markdown."""
    lines = [f"Shopping, week of {result.week_start:%a %d %b}"]
    for title, items in _sections(result):
        lines += ["", title.upper(), *[f"[ ] {item}" for item in items]]
    if result.staples:
        lines += ["", "STAPLES (assumed on hand)", ", ".join(s.name for s in result.staples)]
    if result.checks:
        lines += ["", "CHECK", *result.checks]
    return "\n".join(lines) + "\n"


def shopping_pdf(result: ShoppingListResult, path: Path) -> None:
    """A printable list: one column, large type, as many pages as it needs."""
    text_lines = shopping_text(result).splitlines()
    doc = pymupdf.open()
    per_page = 44
    for start in range(0, max(len(text_lines), 1), per_page):
        page = doc.new_page(width=612, height=792)  # US Letter
        chunk = "\n".join(text_lines[start : start + per_page])
        page.insert_textbox(pymupdf.Rect(54, 54, 558, 750), chunk, fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
