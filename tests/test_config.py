import secrets

import pytest

from carapi.core.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_defaults_to_in_memory_sqlite() -> None:
    assert _settings().database_url == "sqlite+pysqlite:///:memory:"


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARAPI_DATABASE_URL", "sqlite+pysqlite:///./dev.db")
    monkeypatch.setenv("CARAPI_SQL_ECHO", "true")
    s = _settings()
    assert s.database_url == "sqlite+pysqlite:///./dev.db"
    assert s.sql_echo is True


def test_api_keys_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
    monkeypatch.setenv("CARAPI_API_KEYS", f" {a} , {b},, ")
    assert _settings().api_keys == [a, b]


def test_api_keys_default_empty() -> None:
    assert _settings().api_keys == []


def test_rate_limit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARAPI_RATE_LIMIT_REQUESTS", "5")
    monkeypatch.setenv("CARAPI_RATE_LIMIT_WINDOW_SECONDS", "2.5")
    s = _settings()
    assert (s.rate_limit_requests, s.rate_limit_window_seconds) == (5, 2.5)
