"""
Last-line guard on every prediction response: no book-attributed field and
no book name reaches a user (PR #15 review follow-ups 1 and 3, 2026-11-08).

Two passes, both on a copy, never on the cached object:

1. `strip_book_fields` drops, at any depth, every key that carries a
   per-book price or names a book: `books`, `book`, `source_book`, any key
   ending in `_book` or `_books`, and any key that contains a book name (a
   `{"pinnacle": {...}}` map, `pinnacle_close`). The consensus fields beside
   them stay.
2. `scrub_book_strings` nulls any string value that names a book. Prose
   fields (`explanation`, `description`, `reason`, `headline`, `blurb`,
   labels like `market_source`) cannot be filtered field by field, so a
   string that mentions a book is withheld whole rather than edited.

Both return what they removed as JSON-pointer-style paths, so the caller can
log a regression: today's objects are clean, and the guard exists so that a
pipeline change which starts writing book data fails closed, loudly.

This is a backstop, not the contract. The game-detail route is already an
allowlist (game_detail_serializer); `/predictions/latest` is a passthrough,
which is why it needs this.
"""

import re
from typing import Any, List, Tuple

# Sportsbooks and venues whose prices must never be attributed in a response.
# Kalshi is deliberately absent: whether its quotes may be shown is an open
# ruling, and "kalshi" also appears in non-price field names.
BOOK_NAMES = (
    "pinnacle", "fanduel", "draftkings", "bovada", "betmgm", "caesars",
    "betrivers", "pointsbet", "bet365", "espn_dk", "novig", "polymarket",
)
_BOOK_KEYS = {"books", "book", "source_book", *BOOK_NAMES}
_BOOK_RE = re.compile(r"\b(" + "|".join(re.escape(b) for b in BOOK_NAMES) + r")\b",
                      re.IGNORECASE)


def _is_book_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    # Substring, not word match, for keys: "pinnacle_close" has no word
    # boundary before "_close", and any key naming a book is book data.
    return (k in _BOOK_KEYS or k.endswith("_book") or k.endswith("_books")
            or any(b in k for b in BOOK_NAMES))


def strip_book_fields(value: Any, path: str = "") -> Tuple[Any, List[str]]:
    """A copy of `value` without book-keyed fields, and the paths removed."""
    removed: List[str] = []
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            here = f"{path}/{key}"
            if _is_book_key(key):
                removed.append(here)
                continue
            out[key], inner = strip_book_fields(sub, here)
            removed += inner
        return out, removed
    if isinstance(value, list):
        out_list = []
        for i, sub in enumerate(value):
            clean, inner = strip_book_fields(sub, f"{path}/{i}")
            out_list.append(clean)
            removed += inner
        return out_list, removed
    return value, removed


def scrub_book_strings(value: Any, path: str = "") -> Tuple[Any, List[str]]:
    """A copy of `value` with every string that names a book set to None."""
    removed: List[str] = []
    if isinstance(value, str):
        if _BOOK_RE.search(value):
            return None, [path or "/"]
        return value, removed
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            out[key], inner = scrub_book_strings(sub, f"{path}/{key}")
            removed += inner
        return out, removed
    if isinstance(value, list):
        out_list = []
        for i, sub in enumerate(value):
            clean, inner = scrub_book_strings(sub, f"{path}/{i}")
            out_list.append(clean)
            removed += inner
        return out_list, removed
    return value, removed


def guard_response(value: Any) -> Tuple[Any, List[str]]:
    """Both passes; returns the clean copy and every path that was removed."""
    stripped, removed_keys = strip_book_fields(value)
    clean, removed_strings = scrub_book_strings(stripped)
    return clean, removed_keys + removed_strings
