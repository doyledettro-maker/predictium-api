"""GET /predictions/games/{game_id}: only a published game id reaches S3, and
every response is built from an allowlist (2026-10-30).

The read is credentialed, so the id is the only thing standing between a
signed-in user and every other object under game_details/. A refused id must
make NO S3 call and answer exactly like a missing game. The S3 client here is
a stub that records calls; nothing in this file can reach a real bucket.
"""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_with_db
from app.routers import predictions as P
from app.services.prediction_service import PredictionService, is_valid_game_id
from app.services.game_detail_serializer import serialize_game_detail

VALID_ID = "BOS@CLE_2026-03-08"

# A detail object carrying everything that must never reach a user, at the
# top level and nested inside fields that ARE served.
FIXTURE = {
    "prediction_id": "pred-1",
    "game_id": VALID_ID,
    "prediction_timestamp": "2026-03-08T15:00:00Z",
    "model_version": "v2",
    "api_version": "1.0.0",
    "feature_snapshot_ids": ["snap-1"],
    "teams": {
        "home": {"team_id": "CLE", "team_name": "Cavaliers", "predicted_score": 112,
                 "vegas_spread": -4.5},
        "away": {"team_id": "BOS", "team_name": "Celtics", "predicted_score": 108},
    },
    "predictions": {"base_spread": -3.0, "base_total": 220.0, "base_home_win_prob": 0.6,
                    "final_spread": -4.0, "final_total": 220.5,
                    "final_home_win_prob": 0.62, "confidence": "medium",
                    "vegas_total": 219.5},
    "context": {"home_rest_days": 1, "away_rest_days": 2, "home_b2b": False,
                "away_b2b": False, "rest_advantage": "away",
                "source_book": "pinnacle"},
    "key_factors": {"home_net_rtg": 5.1, "away_net_rtg": 4.2, "expected_pace": 99.0,
                    "home_off_delta": 1.0, "away_off_delta": -0.5},
    "vegas": {"spread": 4.5, "total": 219.5, "spread_diff": 0.5, "total_diff": 1.0,
              "books": {"pinnacle": {"spread": -4.5}, "fanduel": {"spread": -5.0}}},
    "odds": {
        "status": "LIVE",
        "opening": {"captured_at": "t", "source_book": "pinnacle", "spread": -4.0,
                    "total": 219.0, "ml_home": -180, "ml_away": 150},
        "current": {"captured_at": "t", "consensus_spread": -4.5,
                    "books": {"pinnacle": {"spread": -4.5}, "draftkings": {"spread": -4.5}}},
        "closing": None,
    },
    "prediction_history": [{
        "timestamp": "t", "model_spread": -4.0, "model_total": 220.5,
        "model_win_prob": 0.62, "model_home_ml": -160, "model_away_ml": 140,
        "vegas_spread": -4.5, "vegas_total": 219.5, "vegas_home_ml": -180,
        "vegas_away_ml": 150, "spread_cover_prob": 0.55, "total_over_prob": 0.52,
        "total_under_prob": 0.48,
    }],
    "spread_decomposition": {"top_contributors": [{"feature": "vegas_spread",
                                                   "raw_value": -4.5}]},
    "prediction_summary": "Pinnacle opened CLE -4.0.",
    "rapm_player_detail": {"anything": "pinnacle"},
    "pinnacle_close": {"spread": -4.5},
}

FORBIDDEN_KEYS = {"vegas", "odds", "source_book", "books", "vegas_spread", "vegas_total",
                  "vegas_home_ml", "vegas_away_ml", "spread_decomposition",
                  "prediction_summary", "rapm_player_detail", "pinnacle_close",
                  "feature_snapshot_ids"}
BOOK_NAMES = ("pinnacle", "fanduel", "draftkings", "bovada")


class FakeS3:
    def __init__(self, objects):
        self.objects, self.calls = objects, []

    def get_object(self, Bucket, Key):
        self.calls.append(Key)
        if Key not in self.objects:
            from botocore.exceptions import ClientError
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = json.dumps(self.objects[Key]).encode()
        return {"Body": SimpleNamespace(read=lambda: body)}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3({f"game_details/{VALID_ID}.json": FIXTURE})
    svc = PredictionService()                 # fresh cache per test
    monkeypatch.setattr(svc, "_get_s3_client", lambda: fake)
    monkeypatch.setattr(P, "prediction_service", svc)
    return fake


def _client(premium: bool):
    app = FastAPI()
    app.include_router(P.router)
    user = SimpleNamespace(
        id="user-1",
        subscription=SimpleNamespace(has_premium_access=premium))
    app.dependency_overrides[get_current_user_with_db] = lambda: user
    return TestClient(app)


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


# --- the id gate ------------------------------------------------------------

@pytest.mark.parametrize("bad_id", [
    "game_detail_BOS_at_CLE_2026-03-08_abc123",       # legacy object name
    "..%2F..%2Flatest",                                # traversal, encoded
    "BOS@CLE_2026-03-08%2F..%2Fx",
    "bos@cle_2026-03-08",                              # lowercase
    "BOS@CLE_2026-03-08%0A",                           # trailing newline
    "BOS@CLE_2026-03-0٨",                         # non-ASCII digit
    "BOSX@CLE_2026-03-08",                             # 4-letter code
    "BOS@CLE_2026-3-8",
    "BOS@CLE_2026-03-08.json",
])
def test_a_non_game_id_is_404_and_never_reaches_s3(s3, bad_id):
    r = _client(premium=True).get(f"/predictions/games/{bad_id}")
    assert r.status_code == 404
    assert s3.calls == []


def test_a_refused_id_answers_exactly_like_a_missing_game(s3):
    c = _client(premium=True)
    missing = c.get("/predictions/games/NYK@MIA_2026-03-08")   # valid shape, absent
    refused = c.get("/predictions/games/game_detail_BOS_at_CLE_2026-03-08_x")
    assert missing.status_code == refused.status_code == 404
    assert missing.json() == {"detail": "Game not found: NYK@MIA_2026-03-08"}
    assert refused.json() == {"detail": "Game not found: game_detail_BOS_at_CLE_2026-03-08_x"}
    assert s3.calls == ["game_details/NYK@MIA_2026-03-08.json"]   # only the valid one


@pytest.mark.parametrize("path_id", [VALID_ID, "BOS%40CLE_2026-03-08"])
def test_a_valid_id_still_works(s3, path_id):
    r = _client(premium=True).get(f"/predictions/games/{path_id}")
    assert r.status_code == 200
    assert r.json()["game_id"] == VALID_ID
    assert s3.calls == [f"game_details/{VALID_ID}.json"]


def test_validator_unit():
    assert is_valid_game_id("LA@GSW_2026-10-30")
    for bad in ("", None, 123, "BOS@CLE_2026-03-08 ", " BOS@CLE_2026-03-08"):
        assert not is_valid_game_id(bad)


# --- the allowlist ------------------------------------------------------------

@pytest.mark.parametrize("premium", [True, False])
def test_no_market_field_or_book_name_reaches_either_tier(s3, premium):
    r = _client(premium=premium).get(f"/predictions/games/{VALID_ID}")
    assert r.status_code == 200
    body = r.json()
    assert not FORBIDDEN_KEYS & set(_all_keys(body))
    text = json.dumps(body).lower()
    assert not any(book in text for book in BOOK_NAMES)


def test_premium_keeps_the_model_detail(s3):
    body = _client(premium=True).get(f"/predictions/games/{VALID_ID}").json()
    assert body["predictions"]["base_spread"] == -3.0
    assert body["key_factors"]["expected_pace"] == 99.0
    assert body["prediction_history"][0] == {
        "timestamp": "t", "model_spread": -4.0, "model_total": 220.5,
        "model_win_prob": 0.62, "model_home_ml": -160, "model_away_ml": 140,
        "spread_cover_prob": 0.55, "total_over_prob": 0.52, "total_under_prob": 0.48}
    assert body["teams"]["home"] == {"team_id": "CLE", "team_name": "Cavaliers",
                                     "predicted_score": 112}


def test_free_tier_gets_the_basic_prediction_only(s3):
    body = _client(premium=False).get(f"/predictions/games/{VALID_ID}").json()
    assert set(body) == {"prediction_id", "game_id", "prediction_timestamp",
                         "teams", "predictions", "context"}
    assert set(body["predictions"]) == {"final_spread", "final_total",
                                        "final_home_win_prob", "confidence"}


def test_a_reshaped_field_cannot_smuggle_a_subtree():
    """A scalar slot holding a dict comes back None, not the dict."""
    data = {"game_id": {"source_book": "pinnacle"}, "teams": "pinnacle"}
    out = serialize_game_detail(data, premium=True)
    assert out == {"game_id": None, "teams": None}
