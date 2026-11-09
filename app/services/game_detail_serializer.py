"""
Allowlist serializer for GET /predictions/games/{game_id}.

The detail object in S3 is written by the model pipeline for its own use, and
older objects carry fields that must never reach a user: consensus and
per-book market lines (`vegas`, `odds.opening/current/closing`, their `books`
and `source_book`), market lines copied into `prediction_history`
(`vegas_*`), and model internals. Some of those market numbers come from
internal-only sources. So the response is built from an ALLOWLIST: a field
reaches the user only if it is named below, at every level of nesting.
Anything not named, including any field a future pipeline adds, is dropped.
Never turn this into a blocklist; a blocklist fails open on the next new
field.

Dropped on purpose, and why:
- `vegas`, `odds`: market lines, consensus and per-book.
- `prediction_history[].vegas_*`: market lines.
- `spread_decomposition`: model features and coefficients, which can include
  market-line features and their raw values.
- `rapm_player_detail`, `rapm_adjustment`: untyped blobs; contents unknown.
- `prediction_summary`: generated prose that may quote market numbers;
  prose cannot be filtered field by field.
- `feature_snapshot_ids`, `move_log_ref`: internal references.
"""

import logging
from typing import Any, Dict

from app.services.response_guard import guard_response

logger = logging.getLogger(__name__)

# player_news carries third-party text (headline, blurb) under a named
# `source`. An item is served only if its source is listed here, and an item
# with no source is never served: provenance unknown. EMPTY until licensing
# and attribution terms are confirmed for each source (PR #15 review,
# follow-up 2). Add a source here only with that confirmation on record;
# never add one because a payload started carrying it.
PLAYER_NEWS_SOURCES: frozenset = frozenset()

# A leaf: kept only if it is a JSON scalar. A dict or list where a scalar is
# expected is dropped (None), so a reshaped field cannot smuggle a subtree.
SCALAR = "scalar"

_TEAM = {"team_id": SCALAR, "team": SCALAR, "team_name": SCALAR, "predicted_score": SCALAR}
_SIDES = lambda item: {"home": item, "away": item}  # noqa: E731

_SCENARIO = {
    "probability": SCALAR, "spread": SCALAR, "total": SCALAR,
    "home_win_prob": SCALAR, "description": SCALAR,
}
_ADJUSTMENT = {
    "player_id": SCALAR, "player_name": SCALAR, "team_id": SCALAR, "status": SCALAR,
    "expected_minutes": SCALAR, "baseline_minutes": SCALAR, "minutes_delta": SCALAR,
    "impact_rating": SCALAR, "point_delta": SCALAR, "explanation": SCALAR,
    "calculation": SCALAR, "rapm_adj": SCALAR,
}
_IMPACT = {
    "player_id": SCALAR, "player_name": SCALAR, "status": SCALAR,
    "baseline_minutes": SCALAR, "scenario_minutes": SCALAR, "minutes_delta": SCALAR,
    "off_impact": SCALAR, "def_impact": SCALAR, "off_contribution": SCALAR,
    "def_contribution": SCALAR, "rapm_adj": SCALAR,
}
_STARTER = {
    "name": SCALAR, "position": SCALAR, "status": SCALAR, "pct_play": SCALAR,
    "expected_minutes": SCALAR, "minutes_source": SCALAR, "o_rapm": SCALAR, "d_rapm": SCALAR,
}
_RECENT_FORM = {
    "last_10_record": SCALAR, "last_10_avg_pts_for": SCALAR,
    "last_10_avg_pts_against": SCALAR, "last_10_net_rtg": SCALAR, "streak": SCALAR,
    "games": [{"date": SCALAR, "opponent": SCALAR, "result": SCALAR,
               "score": SCALAR, "is_home": SCALAR}],
}
_NEWS = {
    "player_name": SCALAR, "source": SCALAR, "team_abbr": SCALAR, "position": SCALAR,
    "injury_type": SCALAR, "status": SCALAR, "headline": SCALAR, "blurb": SCALAR,
    "timestamp": SCALAR, "reason": SCALAR,
}
_INJURY = {"name": SCALAR, "position": SCALAR, "status": SCALAR, "pct_play": SCALAR}
_HISTORY = {
    "timestamp": SCALAR, "model_spread": SCALAR, "model_total": SCALAR,
    "model_win_prob": SCALAR, "model_home_ml": SCALAR, "model_away_ml": SCALAR,
    "spread_cover_prob": SCALAR, "total_over_prob": SCALAR, "total_under_prob": SCALAR,
}

FREE_TIER_SCHEMA: Dict[str, Any] = {
    "prediction_id": SCALAR,
    "game_id": SCALAR,
    "prediction_timestamp": SCALAR,
    "teams": _SIDES(_TEAM),
    "predictions": {
        "final_spread": SCALAR, "final_total": SCALAR,
        "final_home_win_prob": SCALAR, "confidence": SCALAR,
    },
    "context": {
        "home_rest_days": SCALAR, "away_rest_days": SCALAR,
        "home_b2b": SCALAR, "away_b2b": SCALAR, "rest_advantage": SCALAR,
    },
}

PREMIUM_SCHEMA: Dict[str, Any] = {
    **FREE_TIER_SCHEMA,
    "model_version": SCALAR,
    "api_version": SCALAR,
    "predictions": {
        "base_spread": SCALAR, "base_total": SCALAR, "base_home_win_prob": SCALAR,
        "final_spread": SCALAR, "final_total": SCALAR,
        "final_home_win_prob": SCALAR, "confidence": SCALAR,
    },
    "key_factors": {
        "home_net_rtg": SCALAR, "away_net_rtg": SCALAR, "expected_pace": SCALAR,
        "home_off_delta": SCALAR, "away_off_delta": SCALAR,
    },
    "data_provenance": {
        "data_through": SCALAR, "data_refreshed": SCALAR, "possession_source": SCALAR,
        "training_seasons": [SCALAR], "training_games": SCALAR,
    },
    "scenarios": {
        "baseline": _SCENARIO, "all_questionable_out": _SCENARIO,
        "all_questionable_in": _SCENARIO,
    },
    "questionable_players": _SIDES([[SCALAR]]),
    "player_adjustments": _SIDES([_ADJUSTMENT]),
    "player_impact_breakdown": _SIDES([_IMPACT]),
    "starters": _SIDES([_STARTER]),
    "recent_form": _SIDES(_RECENT_FORM),
    "player_news": _SIDES([_NEWS]),
    "injuries": _SIDES([_INJURY]),
    "prediction_history": [_HISTORY],
}


def _project(value: Any, schema: Any) -> Any:
    if schema == SCALAR:
        return value if value is None or isinstance(value, (str, int, float, bool)) else None
    if isinstance(schema, list):
        if not isinstance(value, list):
            return None
        return [_project(item, schema[0]) for item in value]
    if not isinstance(value, dict):
        return None
    return {key: _project(value[key], sub) for key, sub in schema.items() if key in value}


def _vetted_news(news: Any) -> Any:
    if not isinstance(news, dict):
        return news
    return {side: [item for item in items
                   if isinstance(item, dict) and isinstance(item.get("source"), str)
                   and item["source"] in PLAYER_NEWS_SOURCES]
            if isinstance(items, list) else items
            for side, items in news.items()}


def serialize_game_detail(data: Dict[str, Any], *, premium: bool) -> Dict[str, Any]:
    """The game detail a user may see, built from the allowlist for their tier,
    then passed through the book guard as a backstop."""
    out = _project(data, PREMIUM_SCHEMA if premium else FREE_TIER_SCHEMA) or {}
    if "player_news" in out:
        out["player_news"] = _vetted_news(out["player_news"])
    clean, removed = guard_response(out)
    if removed:
        logger.warning("game detail %s: book guard removed %s",
                       data.get("game_id"), removed[:20])
    return clean
