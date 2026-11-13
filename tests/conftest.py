import secrets
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from carapi.core.config import Settings
from carapi.main import create_app

# Random per test run: no key literals in the repo.
TEST_KEY = secrets.token_urlsafe(24)
OTHER_KEY = secrets.token_urlsafe(24)


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"api_keys": [TEST_KEY], "rate_limit_enabled": False}
    # _env_file=None: never read a developer's local .env in tests.
    return Settings(_env_file=None, **(base | overrides))


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Authenticated client (sends X-API-Key), fresh in-memory DB, rate limiting off."""
    with TestClient(create_app(make_settings()), headers={"X-API-Key": TEST_KEY}) as c:
        yield c


@pytest.fixture
def app_client() -> Iterator[Callable[..., TestClient]]:
    """Factory for clients with custom settings; no default auth header."""
    clients: list[TestClient] = []

    def _make(**overrides: Any) -> TestClient:
        c = TestClient(create_app(make_settings(**overrides)))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)
