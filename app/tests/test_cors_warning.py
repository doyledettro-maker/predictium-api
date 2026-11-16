"""Startup CORS warning (2026-11-16): production ran with ALLOWED_ORIGINS unset,
so CORS fell back to localhost and every browser call from the site failed.
The API now logs a warning outside development. Behaviour is unchanged."""

import pytest

from app.config import Settings


def make(monkeypatch, **env):
    for k in ("ALLOWED_ORIGINS", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_development_never_warns(monkeypatch):
    assert make(monkeypatch).cors_origins_warning() is None
    assert make(monkeypatch, APP_ENV="development").cors_origins_warning() is None


def test_unset_outside_development_warns(monkeypatch):
    s = make(monkeypatch, APP_ENV="production")
    w = s.cors_origins_warning()
    assert w and "ALLOWED_ORIGINS is not set" in w and "https://www.predictium.ai" in w
    assert s.cors_origins == ["http://localhost:3000"]  # behaviour unchanged


def test_localhost_only_outside_development_warns(monkeypatch):
    w = make(monkeypatch, APP_ENV="production", ALLOWED_ORIGINS="http://localhost:3000, http://127.0.0.1:3000").cors_origins_warning()
    assert w and "only local origins" in w


@pytest.mark.parametrize("origins", [
    "https://www.predictium.ai,https://predictium.ai",
    "http://localhost:3000,https://www.predictium.ai",
])
def test_site_origin_set_is_quiet(monkeypatch, origins):
    s = make(monkeypatch, APP_ENV="production", ALLOWED_ORIGINS=origins)
    assert s.cors_origins_warning() is None
    assert "https://www.predictium.ai" in s.cors_origins


@pytest.mark.parametrize("origins", [
    "http://[::1",
    "http://localhost:3000,https://www.predictium.ai]",
])
def test_malformed_origin_never_raises(monkeypatch, origins):
    w = make(monkeypatch, APP_ENV="production", ALLOWED_ORIGINS=origins).cors_origins_warning()
    assert w and "cannot be parsed" in w


@pytest.mark.parametrize("origins", ["", " , "])
def test_empty_value_warns(monkeypatch, origins):
    w = make(monkeypatch, APP_ENV="production", ALLOWED_ORIGINS=origins).cors_origins_warning()
    assert w and "empty" in w


@pytest.mark.parametrize("origins", ["http://0.0.0.0:8000", "http://[::1]:3000", "http://localhost:3000,http://[::1]:3000"])
def test_other_local_hosts_count_as_local(monkeypatch, origins):
    w = make(monkeypatch, APP_ENV="production", ALLOWED_ORIGINS=origins).cors_origins_warning()
    assert w and "only local origins" in w
