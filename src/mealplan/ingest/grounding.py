"""Grounding check: extracted ingredient lines must be traceable to the source text."""

import re
import unicodedata

_VULGAR_FRACTION = re.compile(r"(\d)([\u00bc-\u00be\u2150-\u215e])")


def normalize(text: str) -> str:
    text = _VULGAR_FRACTION.sub(r"\1 \2", text)  # keep "1 1/2" apart once NFKC splits the glyph
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2044", "/").lower()
    text = re.sub(r"[^\w/.%\-]+", " ", text)  # drop bullets, quotes, commas, brackets
    return re.sub(r"\s+", " ", text).strip()


def ungrounded_lines(lines: list[str], source_text: str) -> list[str]:
    """Lines whose normalized text does not occur in the normalized source.

    An empty text layer (image-only page) cannot ground anything, so nothing is reported;
    those drafts are marked as vision-sourced for the reviewer instead.
    """
    source = normalize(source_text)
    if len(source) < 20:
        return []
    return [line for line in lines if normalize(line) not in source]
