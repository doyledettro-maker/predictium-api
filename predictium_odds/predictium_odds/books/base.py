"""Shared adapter plumbing: HTTP helper, timestamps, sport config."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_json(url: str, params: dict | None = None,
             headers: dict | None = None, timeout: int = 30):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


@dataclass
class SportConfig:
    """Everything sport-specific an adapter needs, supplied by the repo.

    resolve_team: raw source team string -> canonical abbr/pid, or None
    (unresolved entries are counted in the SourceReport note, not dropped
    silently). event_key: (home, away, start_iso) -> canonical event key.
    """
    sport: str
    resolve_team: Callable[[str], str | None]
    event_key: Callable[[str, str, str], str]
    bovada_path: str = ""            # e.g. "/football/nfl"
    fanduel_page_id: str = ""        # e.g. "nfl"
    fanduel_state: str = "nj"        # per-state sbapi host (unify on nj)
    espn_league: str = ""            # e.g. "football/nfl"
    kalshi_prefix: str = ""          # e.g. "KXNFL" -> KXNFLGAME/SPREAD/TOTAL
    extras: dict = field(default_factory=dict)
