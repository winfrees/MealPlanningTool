"""REC-2: deterministic ingredient-line parser.

Splits a line such as ``"2 (15-ounce) cans chickpeas, drained"`` into quantity, unit, package
size, ingredient name, and prep note. The raw line is always kept. Lines the rules cannot read
leave ``qty``/``unit`` empty rather than guessing; the normalizer flags them for review.
"""

import re
from dataclasses import dataclass
from fractions import Fraction

from mealplan.core.units import UNITS, ConversionError, Dimension, canonical_unit, convert


@dataclass(frozen=True)
class ParsedIngredient:
    raw: str
    name: str
    qty: float | None = None
    qty_max: float | None = None
    unit: str | None = None
    size_qty: float | None = None
    size_unit: str | None = None
    prep_note: str = ""
    optional: bool = False


_UNICODE_FRACTIONS = {
    "½": "1/2",
    "⅓": "1/3",
    "⅔": "2/3",
    "¼": "1/4",
    "¾": "3/4",
    "⅕": "1/5",
    "⅛": "1/8",
    "⅜": "3/8",
    "⅝": "5/8",
    "⅞": "7/8",
}
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_SIZE_WORDS = ("small", "medium", "large")

_NUM = r"\d+\s+\d+/0*[1-9]\d*|\d+/0*[1-9]\d*|\d+(?:\.\d+)?"
_QTY_RE = re.compile(rf"^(?P<a>{_NUM})(?:\s*(?:-|to)\s*(?P<b>{_NUM}))?\s*", re.IGNORECASE)
_WORD_QTY_RE = re.compile(rf"^(?P<w>{'|'.join(_WORD_NUMBERS)}|an?)\s+", re.IGNORECASE)
_UNIT_TOKEN_RE = re.compile(r"^(?P<u>fl\.?\s*oz\.?|fluid\s+ounces?|[A-Za-z]+\.?)(?=[\s,(]|$)\s*")
_HYPHEN_SIZE_RE = re.compile(rf"^(?P<n>{_NUM})\s*-\s*(?P<u>[A-Za-z]+)\.?\s+")
_PAREN_RE = re.compile(r"\(([^()]*)\)")
_PAREN_SIZE_RE = re.compile(rf"^\s*(?P<n>{_NUM})\s*-?\s*(?P<u>[A-Za-z. ]+?)(?:\s+each)?\s*$")
_GLUED_SIZE_RE = re.compile(rf"^({_NUM})\s*([A-Za-z]+)\s+")
_PLUS_RE = re.compile(r"^(?:plus|\+)\s+", re.IGNORECASE)
_BULLET_RE = re.compile("^[\\s\u25a2\u2022*\u00b7\\-\u2013\u2014]+(?=\\S)")
_OPTIONAL_RE = re.compile(r"\(\s*optional\s*\)|,?\s*\boptional\b\s*$", re.IGNORECASE)
_TO_TASTE_RE = re.compile(r"\s+(to taste)$", re.IGNORECASE)


def _number(text: str) -> float:
    total = Fraction(0)
    for part in text.split():
        total += Fraction(part)
    return float(total)


def _normalize(raw: str) -> str:
    text = raw
    for glyph, ascii_frac in _UNICODE_FRACTIONS.items():
        text = re.sub(rf"(\d){glyph}", rf"\1 {ascii_frac}", text)
        text = text.replace(glyph, ascii_frac)
    text = text.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    text = _BULLET_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _size_unit(token: str) -> str | None:
    t = token.strip().rstrip(".").lower()
    if t in ("inch", "inches", "in"):
        return "inch"
    unit = canonical_unit(t)
    if unit is not None and UNITS[unit][0] is not Dimension.COUNT:
        return unit
    return None


def _take_unit(text: str) -> tuple[str | None, str]:
    m = _UNIT_TOKEN_RE.match(text)
    if m:
        unit = canonical_unit(m["u"].replace("fl.", "fl"))
        if unit is not None:
            return unit, text[m.end() :]
    return None, text


def _take_paren_size(text: str) -> tuple[float | None, str | None, str]:
    """Consume a leading "(15-ounce)" or "(15 oz each)" package size."""
    if not text.startswith("("):
        return None, None, text
    close = text.find(")")
    m = _PAREN_SIZE_RE.match(text[1:close])
    if m and (unit := _size_unit(m["u"])) is not None:
        return _number(m["n"]), unit, text[close + 1 :].lstrip()
    return None, None, text


def _take_hyphen_size(text: str) -> tuple[float | None, str | None, str]:
    """Consume a leading "28-ounce" or "1-inch" size."""
    m = _HYPHEN_SIZE_RE.match(text)
    if m and (unit := _size_unit(m["u"])) is not None:
        return _number(m["n"]), unit, text[m.end() :]
    return None, None, text


def parse_ingredient(raw: str) -> ParsedIngredient:
    text = _normalize(raw)

    optional = bool(_OPTIONAL_RE.search(text))
    text = _OPTIONAL_RE.sub("", text).strip()

    qty: float | None = None
    qty_max: float | None = None
    unit: str | None = None
    size_qty: float | None = None
    size_unit: str | None = None

    size_qty, size_unit, text = _take_hyphen_size(text)
    if size_qty is not None:
        qty = 1.0
    elif m := _QTY_RE.match(text):
        qty = _number(m["a"])
        qty_max = _number(m["b"]) if m["b"] else None
        text = text[m.end() :]
    elif (m := _WORD_QTY_RE.match(text)) and (
        m["w"].lower() in _WORD_NUMBERS or _take_unit(text[m.end() :])[0] is not None
    ):
        qty = float(_WORD_NUMBERS.get(m["w"].lower(), 1))
        text = text[m.end() :]

    if qty is not None:
        text = re.sub(r"^x\s+", "", text, flags=re.IGNORECASE)  # UK "1 x 400g tin"
        if size_qty is None:
            size_qty, size_unit, text = _take_paren_size(text)
        if size_qty is None:
            size_qty, size_unit, text = _take_hyphen_size(text)
        m = _GLUED_SIZE_RE.match(text)  # "400g tin"
        if size_qty is None and m and (glued := _size_unit(m[2])):
            unit_after, _ = _take_unit(text[m.end() :])
            if unit_after is not None:
                size_qty, size_unit, text = _number(m[1]), glued, text[m.end() :]

        size_word = ""
        if (m := re.match(rf"^({'|'.join(_SIZE_WORDS)})\s+", text, re.IGNORECASE)) and _take_unit(
            text[m.end() :]
        )[0]:
            size_word, text = m[1].lower(), text[m.end() :]

        unit, text = _take_unit(text)
        if unit is not None and UNITS[unit][0] is Dimension.COUNT and size_qty is None:
            size_qty, size_unit, text = _take_paren_size(text)

        if unit is not None and (m := _PLUS_RE.match(text)):
            extra = _QTY_RE.match(text[m.end() :])
            if extra:
                extra_unit, rest = _take_unit(text[m.end() + extra.end() :])
                try:
                    if extra_unit is not None:
                        qty += convert(_number(extra["a"]), extra_unit, unit)
                        text = rest
                except ConversionError:
                    pass

        text = re.sub(r"^of\s+", "", text, flags=re.IGNORECASE)
        if size_word:
            text = f"{size_word} {text}"

    notes = [n.strip() for n in _PAREN_RE.findall(text) if n.strip()]
    text = re.sub(r"\s+", " ", _PAREN_RE.sub("", text)).strip()

    name, _, prep = text.partition(",")
    name, prep = name.strip(), prep.strip()
    if not prep and (m := _TO_TASTE_RE.search(name)):
        name, prep = name[: m.start()], m[1]

    if unit is None and qty is not None:
        words = name.split()
        if len(words) > 1:
            last = canonical_unit(words[-1])
            if last is not None and UNITS[last][0] is Dimension.COUNT:
                unit, name = last, " ".join(words[:-1])

    prep_note = "; ".join(p for p in [prep, *notes] if p)
    return ParsedIngredient(
        raw=raw,
        name=name.lower(),
        qty=qty,
        qty_max=qty_max,
        unit=unit,
        size_qty=size_qty,
        size_unit=size_unit,
        prep_note=prep_note,
        optional=optional,
    )
