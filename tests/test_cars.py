from typing import Any

import pytest
from fastapi.testclient import TestClient

VIN = "1HGCM82633A004352"


def car_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "vin": VIN,
        "make": "Honda",
        "model": "Accord",
        "year": 2020,
        "price": 21999.99,
        "mileage": 35000,
        "color": "Blue",
    } | overrides


def create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    r = client.post("/cars", json=car_payload(**overrides))
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    return body


def test_create_car(client: TestClient) -> None:
    r = client.post("/cars", json=car_payload(vin=" 1hgcm82633a004352 "))
    assert r.status_code == 201
    body = r.json()
    assert r.headers["location"] == f"/cars/{body['id']}"
    assert body["vin"] == VIN  # trimmed + upper-cased
    assert body["price"] == 21999.99
    assert body["status"] == "available"
    assert body["created_at"].endswith("Z") or body["created_at"].endswith("+00:00")


def test_create_duplicate_vin_conflict(client: TestClient) -> None:
    create(client)
    r = client.post("/cars", json=car_payload())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    assert VIN in r.json()["error"]["message"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"vin": "SHORT"},
        {"vin": "1HGCM82633A00435I"},  # I not allowed
        {"year": 1800},
        {"year": 3000},
        {"price": -1},
        {"price": 1.234},
        {"mileage": -5},
        {"make": ""},
        {"status": "stolen"},
        {"unknown": "x"},
    ],
)
def test_create_validation(client: TestClient, overrides: dict[str, Any]) -> None:
    assert client.post("/cars", json=car_payload(**overrides)).status_code == 422


def test_price_is_exact(client: TestClient) -> None:
    car = create(client, price=19999.07)
    assert client.get(f"/cars/{car['id']}").json()["price"] == 19999.07


def test_list_and_get(client: TestClient) -> None:
    assert client.get("/cars").json()["items"] == []
    a = create(client)
    b = create(client, vin="5YJ3E1EA7KF317000", make="Tesla", model="Model 3")
    assert [c["id"] for c in client.get("/cars").json()["items"]] == [a["id"], b["id"]]
    assert client.get(f"/cars/{b['id']}").json()["make"] == "Tesla"


def test_get_missing(client: TestClient) -> None:
    r = client.get("/cars/999")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    assert r.json()["error"]["message"] == "Car 999 not found"


def test_patch_partial(client: TestClient) -> None:
    car = create(client)
    r = client.patch(f"/cars/{car['id']}", json={"price": 20500, "status": "reserved"})
    assert r.status_code == 200
    body = r.json()
    assert body["price"] == 20500.0
    assert body["status"] == "reserved"
    assert body["make"] == "Honda"  # untouched
    assert body["updated_at"] >= car["updated_at"]


def test_patch_color_to_null(client: TestClient) -> None:
    car = create(client)
    r = client.patch(f"/cars/{car['id']}", json={"color": None})
    assert r.status_code == 200
    assert r.json()["color"] is None


def test_patch_required_field_to_null_rejected(client: TestClient) -> None:
    car = create(client)
    r = client.patch(f"/cars/{car['id']}", json={"make": None})
    assert r.status_code == 422


def test_patch_vin_conflict(client: TestClient) -> None:
    create(client)
    other = create(client, vin="5YJ3E1EA7KF317000")
    assert client.patch(f"/cars/{other['id']}", json={"vin": VIN}).status_code == 409


def test_patch_same_vin_ok(client: TestClient) -> None:
    car = create(client)
    assert client.patch(f"/cars/{car['id']}", json={"vin": VIN}).status_code == 200


def test_patch_missing(client: TestClient) -> None:
    assert client.patch("/cars/999", json={"price": 1}).status_code == 404


def test_delete(client: TestClient) -> None:
    car = create(client)
    assert client.delete(f"/cars/{car['id']}").status_code == 204
    assert client.get(f"/cars/{car['id']}").status_code == 404
    assert client.delete(f"/cars/{car['id']}").status_code == 404
