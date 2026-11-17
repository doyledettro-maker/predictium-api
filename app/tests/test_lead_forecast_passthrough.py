"""NBA latest.json now carries games 2 or more days out whose model numbers
are marked `lead_forecast: "unmeasured"` with a `lead_forecast_reason`.

/predictions/latest serves latest.json whole, through the book guard. The
ruled behaviour is a pass-through that keeps the marker: the guard may drop
book fields beside it, but the marker and its reason must reach the user
unchanged on every lead game, next to the numbers they qualify. The API does
no lead filtering of its own.
"""

import copy

from app.routers import predictions as P
from app.services.prediction_service import PredictionService
from app.services.response_guard import guard_response
from app.tests.test_response_guard import FakeS3, _assert_no_book, _client

MARKER = "unmeasured"
REASON = ("Forecast made 2 or more days before tip-off. Accuracy at this "
          "lead time has not been measured yet.")


def _lead_game(game_id, spread, total, prob, note=None):
    g = {
        "game_id": game_id,
        "predictions": {"spread": spread, "total": total, "home_win_prob": prob},
        "lead_forecast": MARKER,
        "lead_forecast_reason": REASON,
        # Book data beside the marker, so the guard has work to do on the
        # same game and the test shows the marker survives that work.
        "vegas": {"spread": spread + 0.5, "market_source": "consensus",
                  "books": {"pinnacle": {"spread": -spread}}},
        "ml_home_book": "bovada",
    }
    if note is not None:
        g["note"] = note       # book-naming prose: the string scrub nulls it
    return g


def _game(game_id, spread, total, prob):
    return {"game_id": game_id,
            "predictions": {"spread": spread, "total": total, "home_win_prob": prob}}


def _day(date, dow, games):
    """A day block in the live shape (checked against the bucket 2026-11-17)."""
    return {"date": date, "day_of_week": dow, "game_count": len(games),
            "games": games}


# Lead games (2 or more days out): (game_id, spread, total, home_win_prob).
LAL_DEN = ("LAL@DEN_2026-11-19", -3.5, 229.5, 0.61)
PHX_GSW = ("PHX@GSW_2026-11-20", -6.0, 231.5, 0.68)
MIA_CHI = ("MIA@CHI_2026-11-21", 1.5, 214.0, 0.47)
NOTE = "Line first posted at Pinnacle."

# Where each lead game sits in the served object, and which game it is.
# 11-19 is day_three and also days[2]: the same block in both places.
LEAD = {
    "/day_three/games/0": LAL_DEN,
    "/days/2/games/0": LAL_DEN,
    "/days/3/games/0": PHX_GSW,
    "/days/4/games/0": MIA_CHI,
}


def _latest():
    today = _day("2026-11-17", "Tuesday", [_game("NYK@BOS_2026-11-17", -2.0, 224.5, 0.56)])
    tomorrow = _day("2026-11-18", "Wednesday", [_game("DAL@HOU_2026-11-18", 2.5, 226.0, 0.44)])
    day_three = _day("2026-11-19", "Thursday", [_lead_game(*LAL_DEN, note=NOTE)])
    return {
        "status": "ok",
        "meta": {"generated_at": "2026-11-17T12:00:00Z"},
        "hold": {},
        "today": today,
        "tomorrow": tomorrow,
        "day_three": day_three,
        # days[0..2] mirror today, tomorrow and day_three, as on the bucket.
        "days": [
            copy.deepcopy(today), copy.deepcopy(tomorrow), copy.deepcopy(day_three),
            _day("2026-11-20", "Friday", [_lead_game(*PHX_GSW)]),
            _day("2026-11-21", "Saturday", [_lead_game(*MIA_CHI)]),
            _day("2026-11-22", "Sunday", []),
        ],
        "summary": {"total_games": 5},
        "errors": [],
    }


def _at(body, pointer):
    node = body
    for part in pointer.strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _games_with_marker(obj):
    """Every dict anywhere in obj that carries lead_forecast."""
    if isinstance(obj, dict):
        if "lead_forecast" in obj:
            yield obj
        for v in obj.values():
            yield from _games_with_marker(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _games_with_marker(v)


def _assert_marker_kept(body):
    for where, (game_id, spread, total, prob) in LEAD.items():
        game = _at(body, where)
        assert game["game_id"] == game_id, where
        assert game["lead_forecast"] == MARKER, where
        assert game["lead_forecast_reason"] == REASON, where
        assert game["predictions"] == {"spread": spread, "total": total,
                                       "home_win_prob": prob}, where
        if game_id == LAL_DEN[0]:
            assert game["note"] is None, where      # book prose scrubbed
    # No lead game lost and none gained a marker it did not have.
    assert len(list(_games_with_marker(body))) == len(LEAD)


# Every path the guard must report: the two book fields on each lead game,
# and the book-naming note on both copies of the 11-19 game.
EXPECTED_REMOVED = sorted(
    [f"{w}/vegas/books" for w in LEAD] + [f"{w}/ml_home_book" for w in LEAD]
    + ["/day_three/games/0/note", "/days/2/games/0/note"])


def test_guard_keeps_the_lead_marker_beside_the_numbers():
    src = _latest()
    before = copy.deepcopy(src)
    clean, removed = guard_response(src)
    _assert_marker_kept(clean)
    _assert_no_book(clean)
    # The guard did strip the book data and the book prose on those games.
    assert sorted(removed) == EXPECTED_REMOVED
    assert src == before                     # a copy, never the input


def test_latest_endpoint_serves_the_lead_marker_unchanged(monkeypatch):
    s = PredictionService()
    monkeypatch.setattr(s, "_get_s3_client",
                        lambda: FakeS3({"latest.json": _latest()}))
    monkeypatch.setattr(P, "prediction_service", s)
    r = _client().get("/predictions/latest")
    assert r.status_code == 200
    body = r.json()
    _assert_marker_kept(body)
    _assert_no_book(body)
    # The pass-through serves the whole object: every day is still there,
    # same-day games carry no marker, and nothing was filtered by lead time.
    assert set(body) == set(_latest())
    assert [len(d["games"]) for d in body["days"]] == [1, 1, 1, 1, 1, 0]
    assert [d["game_count"] for d in body["days"]] == [1, 1, 1, 1, 1, 0]
    for day in ("today", "tomorrow"):
        assert "lead_forecast" not in body[day]["games"][0]
    assert body["days"][2] == body["day_three"]   # mirror kept after the guard
