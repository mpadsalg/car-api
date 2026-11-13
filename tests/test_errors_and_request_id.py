import logging
from collections.abc import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from carapi.core.config import Settings
from carapi.main import create_app


def test_validation_error_format(client: TestClient) -> None:
    r = client.get("/cars", params={"size": 0})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["message"] == "Request validation failed"
    assert err["request_id"] == r.headers["x-request-id"]
    assert err["details"][0]["loc"] == ["query", "size"]
    assert set(err["details"][0]) == {"loc", "msg", "type"}


def test_unknown_route_404_format(client: TestClient) -> None:
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_method_not_allowed_format(client: TestClient) -> None:
    r = client.put("/cars")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "method_not_allowed"


def test_request_id_generated(client: TestClient) -> None:
    a = client.get("/health").headers["x-request-id"]
    b = client.get("/health").headers["x-request-id"]
    assert len(a) == 32 and a != b


def test_request_id_propagated(client: TestClient) -> None:
    r = client.get("/cars/999", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123"
    assert r.json()["error"]["request_id"] == "abc-123"


@pytest.mark.parametrize("bad", ["has space", "x" * 129, "semi;colon"])
def test_unsafe_request_id_replaced(client: TestClient, bad: str) -> None:
    rid = client.get("/health", headers={"X-Request-ID": bad}).headers["x-request-id"]
    assert rid != bad and len(rid) == 32


@pytest.fixture
def boom_client() -> Callable[[], TestClient]:
    def _make() -> TestClient:
        app: FastAPI = create_app(Settings(_env_file=None, rate_limit_enabled=False))

        @app.get("/boom")
        def boom() -> None:
            raise RuntimeError("secret internals")

        return TestClient(app)

    return _make


def test_unhandled_error_is_json_500(
    boom_client: Callable[[], TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    logging.getLogger("carapi").propagate = True  # let caplog see it
    try:
        with caplog.at_level(logging.ERROR, logger="carapi.errors"):
            r = boom_client().get("/boom", headers={"X-Request-ID": "req-500"})
    finally:
        logging.getLogger("carapi").propagate = False
    assert r.status_code == 500
    assert r.headers["x-request-id"] == "req-500"
    body = r.json()
    assert body == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "req-500",
        }
    }
    assert "secret internals" not in r.text
    assert any("Unhandled error on GET /boom" in rec.message for rec in caplog.records)


def test_access_log_has_request_id(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger("carapi").propagate = True
    try:
        with caplog.at_level(logging.INFO, logger="carapi.access"):
            client.get("/health", headers={"X-Request-ID": "log-me"})
    finally:
        logging.getLogger("carapi").propagate = False
    rec = next(r for r in caplog.records if r.name == "carapi.access")
    assert rec.getMessage().startswith("GET /health 200 ")
