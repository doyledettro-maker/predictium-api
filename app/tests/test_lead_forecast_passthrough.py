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


def _lead_game(game_id, spread, total, prob):
    return {
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


def _same_day_game():
    return {"game_id": "NYK@BOS_2026-11-17",
            "predictions": {"spread": -2.0, "total": 224.5, "home_win_prob": 0.56}}


# Lead games, by where they sit in latest.json: (path, game_id, numbers).
LEAD = {
    "day_three": ("LAL@DEN_2026-11-19", -3.5, 229.5, 0.61),
    "days/2": ("MIA@CHI_2026-11-19", 1.5, 214.0, 0.47),
    "days/3": ("PHX@GSW_2026-11-20", -6.0, 231.5, 0.68),
}


def _latest():
    lead = {k: _lead_game(*v) for k, v in LEAD.items()}
    return {
        "status": "ok",
        "meta": {"generated_at": "2026-11-17T12:00:00Z"},
        "today": {"games": [_same_day_game()]},
        "tomorrow": {"games": []},
        "day_three": {"date": "2026-11-19", "games": [lead["day_three"]]},
        "days": [
            {"date": "2026-11-17", "games": [_same_day_game()]},
            {"date": "2026-11-18", "games": []},
            {"date": "2026-11-19", "games": [lead["days/2"]]},
            {"date": "2026-11-20", "games": [lead["days/3"]]},
        ],
    }


def _lead_games_in(body):
    """(path, game) for every lead game, found by where the fixture put it."""
    return [
        ("day_three", body["day_three"]["games"][0]),
        ("days/2", body["days"][2]["games"][0]),
        ("days/3", body["days"][3]["games"][0]),
    ]


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
    for where, game in _lead_games_in(body):
        game_id, spread, total, prob = LEAD[where]
        assert game["game_id"] == game_id, where
        assert game["lead_forecast"] == MARKER, where
        assert game["lead_forecast_reason"] == REASON, where
        assert game["predictions"] == {"spread": spread, "total": total,
                                       "home_win_prob": prob}, where
    # No lead game lost and none gained a marker it did not have.
    assert len(list(_games_with_marker(body))) == len(LEAD)


def test_guard_keeps_the_lead_marker_beside_the_numbers():
    src = _latest()
    before = copy.deepcopy(src)
    clean, removed = guard_response(src)
    _assert_marker_kept(clean)
    _assert_no_book(clean)
    # The guard did strip the book data on those same games.
    assert all(p.endswith(("/books", "/ml_home_book")) for p in removed)
    assert len(removed) == 2 * len(LEAD)
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
    assert [len(d["games"]) for d in body["days"]] == [1, 0, 1, 1]
    assert "lead_forecast" not in body["today"]["games"][0]
