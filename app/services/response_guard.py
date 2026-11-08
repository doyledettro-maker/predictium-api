"""
Last-line guard on every prediction response: no book-attributed field and
no book name reaches a user (PR #15 review follow-ups 1 and 3, 2026-11-08).

Two passes, both on a copy, never on the cached object:

1. `strip_book_fields` drops, at any depth, every key that carries a
   per-book price or names a book: `books`, `book`, `source_book`, any key
   ending in `_book` or `_books`, and any key that contains a book name once
   spaces, underscores and hyphens are removed (a `{"pinnacle": {...}}` map,
   `pinnacle_close`, `draft_kings_line`). The consensus fields beside them
   stay.
2. `scrub_book_strings` nulls any string value that names a book. Prose
   fields (`explanation`, `description`, `reason`, `headline`, `blurb`,
   labels like `market_source`) cannot be filtered field by field, so a
   string that mentions a book is withheld whole rather than edited. That
   is intended fail-closed behaviour: a consumer must treat any string field
   as nullable.

Both return what they removed as JSON-pointer-style paths, so the caller can
log a regression. Only a removal that carried data is reported: an empty
book field (`books: {}` is on every game in today's latest.json) is dropped
silently, so the log stays quiet until something real appears.

This is a backstop, not the contract. The game-detail route is already an
allowlist (game_detail_serializer); `/predictions/latest` is a passthrough,
which is why it needs this.

Recursion: the walk is recursive, so an object nested deeper than Python's
recursion limit (about 1000 levels) raises and the request fails with a 500.
That fails closed, and no S3 object this API reads comes near that depth.
"""

import re
from typing import Any, List, Tuple

# Sportsbooks and venues whose prices must never be attributed in a response,
# as patterns where a multi-word name may be written with a space, an
# underscore, a hyphen or nothing between its parts ("Fan Duel", "bet_365").
# Kalshi is deliberately absent: whether its quotes may be shown is an open
# ruling, and "kalshi" also appears in non-price field names. "Circa" is
# absent too: as an English word it is common in prose and would null
# ordinary text.
_SEP = r"[\s_-]*"
_BOOK_PATTERNS = (
    "pinnacle", "bovada", "caesars", "novig", "polymarket", "fanatics",
    "betfair", "unibet", "superbook",
    f"fan{_SEP}duel", f"draft{_SEP}kings", f"bet{_SEP}mgm", f"bet{_SEP}rivers",
    f"points{_SEP}bet", f"bet{_SEP}365", f"bet{_SEP}online",
    f"espn{_SEP}(?:dk|bet)", f"hard{_SEP}rock{_SEP}bet", f"william{_SEP}hill",
)
# Whole-word, where "word" ends at a letter or digit: an underscore is a
# separator here, so "FanDuel_DK" matches and "Novigrad" or "Pinnacles" do not.
_BOOK_RE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(_BOOK_PATTERNS) + r")(?![a-z0-9])",
    re.IGNORECASE)
# The same names compacted, for keys ("draft_kings_line" -> "draftkingsline").
BOOK_NAMES = (
    "pinnacle", "bovada", "caesars", "novig", "polymarket", "fanatics",
    "betfair", "unibet", "superbook", "fanduel", "draftkings", "betmgm",
    "betrivers", "pointsbet", "bet365", "betonline", "espndk", "espnbet",
    "hardrockbet", "williamhill",
)
_BOOK_KEYS = {"books", "book", "sourcebook"}
_SEP_RE = re.compile(r"[\s_-]+")


def _is_book_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    k = key.lower()
    if k.endswith("_book") or k.endswith("_books"):
        return True
    tokens = [t for t in _SEP_RE.split(k) if t]
    compact = "".join(tokens)
    if compact in _BOOK_KEYS:
        return True
    # Novig only as its own token: compacting "implied_prob_home_no_vig" (a
    # de-vigged consensus field) would otherwise read as the venue.
    if any(t.startswith("novig") for t in tokens):
        return True
    # Substring, not word match, for every other name: "pinnacle_close" and
    # "draft_kings_line" are book data.
    return any(b in compact for b in BOOK_NAMES if b != "novig")


def _carries_data(value: Any) -> bool:
    """False for an empty book field, which is dropped without a report."""
    return value not in (None, "", {}, [])


def strip_book_fields(value: Any, path: str = "") -> Tuple[Any, List[str]]:
    """A copy of `value` without book-keyed fields, and the paths of the ones
    that carried data (empty ones are dropped but not reported)."""
    removed: List[str] = []
    if isinstance(value, dict):
        out = {}
        for key, sub in value.items():
            here = f"{path}/{key}"
            if _is_book_key(key):
                if _carries_data(sub):
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
    """Both passes; returns the clean copy and every path that was removed
    with data in it."""
    stripped, removed_keys = strip_book_fields(value)
    clean, removed_strings = scrub_book_strings(stripped)
    return clean, removed_keys + removed_strings
