"""No book-attributed field and no book name reaches a user, on either route
(PR #15 review follow-ups 1 to 3, 2026-11-08).

1. /predictions/latest is a passthrough of latest.json, so a recursive guard
   drops book-keyed fields from a copy and logs what it removed.
2. player_news items are served only from a vetted source list (empty today).
3. Every string either route serves is scanned for book names.
The S3 client is a recording stub; nothing here reaches a real bucket.
"""
import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user, get_current_user_with_db
from app.routers import predictions as P
from app.services import game_detail_serializer as GDS
from app.services.prediction_service import PredictionService
from app.services.response_guard import (BOOK_NAMES, guard_response,
                                         scrub_book_strings, strip_book_fields)

GAME_ID = "BOS@CLE_2026-03-08"

LATEST = {
    "status": "ok",
    "meta": {"generated_at": "2026-11-08T12:00:00Z", "market_source": "pinnacle"},
    "today": {"games": [{
        "game_id": GAME_ID,
        "predictions": {"spread": 4.0, "total": 220.5, "home_win_prob": 0.62},
        "vegas": {"spread": 4.5, "total": 219.5, "spread_diff": 0.5,
                  "moneyline_home": -180, "odds_frozen": False,
                  "market_source": "consensus",
                  "books": {"pinnacle": {"spread": -4.5}, "fanduel": {"spread": -5.0}}},
        "odds": {"status": "LIVE",
                 "opening": {"captured_at": "t", "source_book": "pinnacle", "spread": -4.0},
                 "current": {"captured_at": "t", "consensus_spread": -4.5,
                             "books": {"draftkings": {"spread": -4.5}}},
                 "closing": None},
        "ml_home_book": "bovada",
        "pinnacle_close": {"spread": -4.5},
        "kalshi_listed": True,
        "note": "Line moved after Pinnacle opened.",
    }]},
}

DETAIL = {
    "game_id": GAME_ID,
    "teams": {"home": {"team_id": "CLE", "team_name": "Cavaliers", "predicted_score": 112},
              "away": {"team_id": "BOS", "team_name": "Celtics", "predicted_score": 108}},
    "predictions": {"final_spread": -4.0, "final_total": 220.5,
                    "final_home_win_prob": 0.62, "confidence": "medium"},
    "context": {"home_rest_days": 1, "away_rest_days": 2, "home_b2b": False,
                "away_b2b": False, "rest_advantage": "away"},
    "scenarios": {"baseline": {"probability": 0.7, "spread": -4.0, "total": 220.5,
                               "home_win_prob": 0.62,
                               "description": "Baseline vs the FanDuel number"}},
    "player_adjustments": {"home": [{"player_name": "A", "point_delta": -1.2,
                                     "explanation": "Out per DraftKings report",
                                     "calculation": "1.2 pts"}], "away": []},
    "player_news": {"home": [
        {"player_name": "A", "source": "rotowire", "headline": "A out", "blurb": "Knee."},
        {"player_name": "B", "source": "unknownsite", "headline": "B probable", "blurb": "x"},
        {"player_name": "C", "headline": "No source", "blurb": "y"},
    ], "away": []},
}


def _served_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _served_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _served_strings(v)


def _assert_no_book(body):
    for s in _served_strings(body):
        low = s.lower()
        assert not any(b in low for b in BOOK_NAMES), s


class FakeS3:
    def __init__(self, objects):
        self.objects, self.calls = objects, []

    def get_object(self, Bucket, Key):
        self.calls.append(Key)
        body = json.dumps(self.objects[Key]).encode()
        return {"Body": SimpleNamespace(read=lambda: body)}


@pytest.fixture
def svc(monkeypatch):
    s = PredictionService()
    fake = FakeS3({"latest.json": LATEST, f"game_details/{GAME_ID}.json": DETAIL})
    monkeypatch.setattr(s, "_get_s3_client", lambda: fake)
    monkeypatch.setattr(P, "prediction_service", s)
    return s


def _client(premium=True):
    app = FastAPI()
    app.include_router(P.router)
    app.dependency_overrides[get_current_user] = lambda: {"sub": "user-1"}
    app.dependency_overrides[get_current_user_with_db] = lambda: SimpleNamespace(
        id="user-1", subscription=SimpleNamespace(has_premium_access=premium))
    return TestClient(app)


# --- 1. /predictions/latest -------------------------------------------------

def test_latest_drops_book_fields_and_keeps_the_consensus(svc):
    body = _client().get("/predictions/latest").json()
    game = body["today"]["games"][0]
    _assert_no_book(body)
    assert game["vegas"]["spread"] == 4.5 and game["vegas"]["total"] == 219.5
    assert game["odds"]["current"]["consensus_spread"] == -4.5
    assert "books" not in game["vegas"] and "source_book" not in game["odds"]["opening"]
    assert game["kalshi_listed"] is True              # not a book field
    assert game["note"] is None and body["meta"]["market_source"] is None


def test_latest_guard_never_mutates_the_cached_object(svc):
    _client().get("/predictions/latest")
    cached = svc._cache["latest"]
    assert cached["today"]["games"][0]["vegas"]["books"]["pinnacle"] == {"spread": -4.5}


def test_latest_guard_logs_what_it_removed(svc, caplog):
    with caplog.at_level("WARNING"):
        _client().get("/predictions/latest")
    assert "book guard removed" in caplog.text
    assert "/today/games/0/vegas/books" in caplog.text


# --- 2. player_news provenance ----------------------------------------------

def test_news_from_an_unvetted_source_is_never_served(svc):
    body = _client().get(f"/predictions/games/{GAME_ID}").json()
    assert body["player_news"] == {"home": [], "away": []}


def test_a_vetted_source_is_served_and_others_still_drop(svc, monkeypatch):
    monkeypatch.setattr(GDS, "PLAYER_NEWS_SOURCES", frozenset({"rotowire"}))
    body = _client().get(f"/predictions/games/{GAME_ID}").json()
    assert [n["player_name"] for n in body["player_news"]["home"]] == ["A"]


# --- 3. served strings ------------------------------------------------------

@pytest.mark.parametrize("premium", [True, False])
def test_no_served_string_on_game_detail_names_a_book(svc, premium):
    body = _client(premium).get(f"/predictions/games/{GAME_ID}").json()
    _assert_no_book(body)
    if premium:
        assert body["scenarios"]["baseline"]["description"] is None
        assert body["player_adjustments"]["home"][0]["explanation"] is None
        assert body["player_adjustments"]["home"][0]["calculation"] == "1.2 pts"


def test_scrub_is_whole_word_and_case_blind():
    clean, removed = scrub_book_strings({"a": "PINNACLE line", "b": "pinnacles of form",
                                         "c": "Bet365 says", "d": ["fine", "via fanduel"]})
    assert clean == {"a": None, "b": "pinnacles of form", "c": None, "d": ["fine", None]}
    assert removed == ["/a", "/c", "/d/1"]


def test_strip_catches_every_book_key_shape():
    clean, removed = strip_book_fields({"books": 1, "book": 2, "source_book": 3,
                                        "ml_a_book": 4, "best_books": 5, "Pinnacle": 6,
                                        "novig_price": 7, "consensus_spread": -4.5})
    assert clean == {"consensus_spread": -4.5}
    assert len(removed) == 7


def test_guard_on_a_clean_object_changes_nothing():
    obj = {"meta": {"x": 1}, "games": [{"spread": -4.5, "note": "rest edge"}]}
    clean, removed = guard_response(copy.deepcopy(obj))
    assert clean == obj and removed == []


# --- review round 2 (2026-11-08) --------------------------------------------

LIVE_SHAPE = {"today": {"games": [{
    "vegas": {"spread": 4.5, "total": 219.5, "market_source": "public_consensus",
              "implied_prob_home_no_vig": 0.61, "books": {}},
    "odds": {"current": {"consensus_spread": -4.5, "books": {}}},
}]}}


def test_empty_book_fields_drop_silently():
    """Live latest.json carries books: {} on every game; that is not a
    regression and must not be reported."""
    clean, removed = guard_response(copy.deepcopy(LIVE_SHAPE))
    game = clean["today"]["games"][0]
    assert removed == []
    assert "books" not in game["vegas"] and "books" not in game["odds"]["current"]
    assert game["vegas"]["implied_prob_home_no_vig"] == 0.61   # de-vig, not Novig
    assert game["vegas"]["market_source"] == "public_consensus"


def test_live_shaped_latest_logs_nothing(monkeypatch, caplog):
    s = PredictionService()
    monkeypatch.setattr(s, "_get_s3_client", lambda: FakeS3({"latest.json": LIVE_SHAPE}))
    monkeypatch.setattr(P, "prediction_service", s)
    with caplog.at_level("WARNING"):
        assert _client().get("/predictions/latest").status_code == 200
    assert "book guard" not in caplog.text


def test_a_non_empty_book_field_is_still_reported():
    _, removed = strip_book_fields({"vegas": {"books": {"pinnacle": {"spread": -4.5}},
                                              "source_book": "", "best_book": None}})
    assert removed == ["/vegas/books"]


@pytest.mark.parametrize("text", [
    "Draft Kings has it -4", "per Fan Duel", "bet_365 moved", "FanDuel_DK feed",
    "Hard Rock Bet line", "ESPN BET", "espn-dk", "William Hill", "BetOnline",
    "Fanatics opened", "Betfair exchange", "Unibet", "SuperBook",
])
def test_name_variants_are_caught(text):
    assert scrub_book_strings({"s": text})[0] == {"s": None}


@pytest.mark.parametrize("text", [
    "Novigrad", "pinnacles of form", "circa 2019", "no-vig price", "fan of the duel",
])
def test_ordinary_text_passes(text):
    assert scrub_book_strings({"s": text})[0] == {"s": text}


def test_key_variants_and_legit_de_vig_keys():
    clean, removed = strip_book_fields({
        "draft_kings_line": 1, "espn-bet": 2, "hardRockBet": 3, "novigPrice": 4,
        "implied_prob_home_no_vig": 0.61, "impliedProbAwayNoVig": 0.39,
        "kalshi_listed": True, "spread": -4.5})
    assert clean == {"implied_prob_home_no_vig": 0.61, "impliedProbAwayNoVig": 0.39,
                     "kalshi_listed": True, "spread": -4.5}
    assert len(removed) == 4


def test_a_non_string_news_source_is_never_served(monkeypatch):
    monkeypatch.setattr(GDS, "PLAYER_NEWS_SOURCES", frozenset({"rotowire"}))
    out = GDS._vetted_news({"home": [{"source": ["rotowire"]}, {"source": None},
                                     {"source": "rotowire", "headline": "ok"}]})
    assert out == {"home": [{"source": "rotowire", "headline": "ok"}]}
