"""Follower snapshot ingest heals a missing table and never returns a bare
500 (Doyle, 2026-10-13: Claudia's daily follower push had been failing)."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import ProgrammingError

from app.db.database import get_db
from app.routers import analytics as A


class FakeSession:
    async def rollback(self):
        pass


def _client(monkeypatch):
    app = FastAPI()
    app.include_router(A.router)

    async def fake_db():
        yield FakeSession()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[A.require_analytics_key] = lambda: None
    return TestClient(app)


def test_missing_table_is_created_and_the_insert_retried(monkeypatch):
    calls, created = [], []

    async def record(db, **kw):
        calls.append(kw)
        if len(calls) == 1:
            raise ProgrammingError("INSERT", {}, Exception('relation "twitter_follower_snapshots" does not exist'))
        return {"id": 1, "followers": kw["followers"]}

    class Conn:
        async def run_sync(self, fn, **kw):
            created.append(kw["tables"])

    class Eng:
        @asynccontextmanager
        async def begin(self):
            yield Conn()

    monkeypatch.setattr(A.analytics_service, "record_twitter_followers", record)
    monkeypatch.setattr(A, "engine", Eng())
    r = _client(monkeypatch).post("/analytics/twitter/followers", json={"followers": 123})
    assert r.status_code == 202, r.text
    assert len(calls) == 2 and created
    assert r.json()["snapshot"]["followers"] == 123


def test_other_failures_name_themselves(monkeypatch):
    async def record(db, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(A.analytics_service, "record_twitter_followers", record)
    r = _client(monkeypatch).post("/analytics/twitter/followers", json={"followers": 1})
    assert r.status_code == 500
    assert r.json()["detail"] == "follower snapshot not stored: RuntimeError"
