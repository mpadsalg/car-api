from typing import Any

import pytest
from fastapi.testclient import TestClient

# (vin, make, model, year, price, mileage, color, status)
FLEET = [
    ("1HGCM82633A004352", "Honda", "Accord", 2020, 21999.99, 35000, "Blue", "available"),
    ("2HGFC2F59JH000001", "Honda", "Civic", 2018, 15500.00, 62000, "Red", "sold"),
    ("5YJ3E1EA7KF317000", "Tesla", "Model 3", 2022, 38900.00, 12000, "White", "available"),
    ("WBA8E9G50GNT00001", "BMW", "330i", 2016, 17250.50, 88000, None, "reserved"),
    ("JTDKN3DU0A0000001", "Toyota", "Prius", 2020, 18999.00, 45000, "Silver", "available"),
    ("1FTFW1ET5DFC00001", "Ford", "F-150", 2013, 14000.00, 140000, "Black", "available"),
]


@pytest.fixture
def fleet(client: TestClient) -> dict[str, int]:
    ids: dict[str, int] = {}
    for vin, make, model, year, price, mileage, color, status in FLEET:
        r = client.post(
            "/cars",
            json={
                "vin": vin,
                "make": make,
                "model": model,
                "year": year,
                "price": price,
                "mileage": mileage,
                "color": color,
                "status": status,
            },
        )
        assert r.status_code == 201, r.text
        ids[model] = r.json()["id"]
    return ids


def models(client: TestClient, **params: Any) -> list[str]:
    r = client.get("/cars", params=params)
    assert r.status_code == 200, r.text
    return [c["model"] for c in r.json()["items"]]


def test_default_envelope(client: TestClient, fleet: dict[str, int]) -> None:
    body = client.get("/cars").json()
    assert body["total"] == 6
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["pages"] == 1
    assert [c["id"] for c in body["items"]] == sorted(fleet.values())


def test_empty_result_pages_zero(client: TestClient) -> None:
    body = client.get("/cars").json()
    assert body == {"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}


def test_make_case_insensitive(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, make="honda") == ["Accord", "Civic"]


def test_model_and_color_exact(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, model="model 3") == ["Model 3"]
    assert models(client, color="RED") == ["Civic"]
    assert models(client, model="Model") == []  # exact, not substring


def test_q_substring_across_fields(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, q="ond") == ["Accord", "Civic"]  # make
    assert models(client, q="f-1") == ["F-150"]  # model
    assert models(client, q="5yj3") == ["Model 3"]  # vin
    assert models(client, q="silv") == ["Prius"]  # color


def test_q_escapes_wildcards(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, q="%") == []
    assert models(client, q="_") == []


def test_status_multi(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, status="sold") == ["Civic"]
    assert models(client, status=["sold", "reserved"]) == ["Civic", "330i"]


def test_ranges(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, year_min=2020) == ["Accord", "Model 3", "Prius"]
    assert models(client, year_min=2018, year_max=2020) == ["Accord", "Civic", "Prius"]
    assert models(client, price_max=17250.50) == ["Civic", "330i", "F-150"]
    assert models(client, price_min=17250.51, price_max=22000) == ["Accord", "Prius"]
    assert models(client, mileage_max=45000) == ["Accord", "Model 3", "Prius"]
    assert models(client, mileage_min=100000) == ["F-150"]


def test_combined_filters(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, status="available", year_min=2020, price_max=25000) == ["Accord", "Prius"]


def test_sort_desc_and_multi(client: TestClient, fleet: dict[str, int]) -> None:
    assert models(client, sort="-price") == ["Model 3", "Accord", "Prius", "330i", "Civic", "F-150"]
    # year desc, then mileage asc for the 2020 tie
    assert models(client, sort="-year,mileage")[:3] == ["Model 3", "Accord", "Prius"]
    assert models(client, sort="-year,-mileage")[:3] == ["Model 3", "Prius", "Accord"]
    assert models(client, sort="make")[:3] == ["330i", "F-150", "Accord"]


def test_pagination(client: TestClient, fleet: dict[str, int]) -> None:
    p1 = client.get("/cars", params={"sort": "price", "size": 4}).json()
    p2 = client.get("/cars", params={"sort": "price", "size": 4, "page": 2}).json()
    p3 = client.get("/cars", params={"sort": "price", "size": 4, "page": 3}).json()
    assert (p1["total"], p1["pages"]) == (6, 2)
    assert [c["model"] for c in p1["items"]] == ["F-150", "Civic", "330i", "Prius"]
    assert [c["model"] for c in p2["items"]] == ["Accord", "Model 3"]
    assert p3["items"] == [] and p3["total"] == 6


def test_total_reflects_filter_not_page(client: TestClient, fleet: dict[str, int]) -> None:
    body = client.get("/cars", params={"make": "honda", "size": 1}).json()
    assert body["total"] == 2
    assert body["pages"] == 2
    assert len(body["items"]) == 1


@pytest.mark.parametrize(
    "params",
    [
        {"sort": "vin"},
        {"sort": "price,,year"},
        {"sort": "price,-price"},
        {"page": 0},
        {"size": 0},
        {"size": 101},
        {"year_min": 2021, "year_max": 2020},
        {"price_min": 10, "price_max": 5},
        {"mileage_min": 10, "mileage_max": 5},
        {"price_min": -1},
        {"status": "stolen"},
        {"q": "   "},
        {"bogus": "1"},
    ],
)
def test_invalid_params(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/cars", params=params).status_code == 422
