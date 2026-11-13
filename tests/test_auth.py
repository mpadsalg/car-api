from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from carapi.core.security import is_valid_key
from tests.conftest import OTHER_KEY, TEST_KEY

CAR = {
    "vin": "1HGCM82633A004352",
    "make": "Honda",
    "model": "Accord",
    "year": 2020,
    "price": 21999.99,
    "mileage": 35000,
}


@pytest.fixture
def anon(app_client: Callable[..., TestClient]) -> TestClient:
    return app_client(api_keys=[TEST_KEY, OTHER_KEY])


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [("post", "/cars", CAR), ("patch", "/cars/1", {"price": 1}), ("delete", "/cars/1", None)],
)
def test_writes_require_key(anon: TestClient, method: str, path: str, body: object) -> None:
    r = anon.request(method, path, json=body)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "ApiKey"
    err = r.json()["error"]
    assert err["code"] == "unauthorized"
    assert err["message"] == "Missing X-API-Key header"


def test_invalid_key(anon: TestClient) -> None:
    r = anon.post("/cars", json=CAR, headers={"X-API-Key": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "Invalid API key"


def test_any_configured_key_works(anon: TestClient) -> None:
    assert anon.post("/cars", json=CAR, headers={"X-API-Key": OTHER_KEY}).status_code == 201
    r = anon.patch("/cars/1", json={"price": 1}, headers={"X-API-Key": TEST_KEY})
    assert r.status_code == 200


def test_auth_checked_before_existence(anon: TestClient) -> None:
    # No info leak: unauthenticated caller can't probe which ids exist.
    assert anon.delete("/cars/999").status_code == 401


def test_reads_are_public(anon: TestClient) -> None:
    assert anon.get("/cars").status_code == 200
    assert anon.get("/cars/1").status_code == 404
    assert anon.get("/health").status_code == 200


def test_no_keys_configured_rejects_writes(app_client: Callable[..., TestClient]) -> None:
    c = app_client(api_keys=[])
    assert c.post("/cars", json=CAR, headers={"X-API-Key": ""}).status_code == 401
    assert c.post("/cars", json=CAR, headers={"X-API-Key": "anything"}).status_code == 401


def test_is_valid_key() -> None:
    assert is_valid_key("b", ["a", "b"])
    assert not is_valid_key("c", ["a", "b"])
    assert not is_valid_key("", ["a"])
    assert not is_valid_key(None, ["a"])
    assert not is_valid_key("a", [])


def test_openapi_declares_api_key_scheme(anon: TestClient) -> None:
    spec = anon.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert schemes["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Required for writes",
    }
    assert "security" in spec["paths"]["/cars"]["post"]
    assert "security" not in spec["paths"]["/cars"]["get"]
