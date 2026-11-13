import csv
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from carapi.main import SeedError, create_app
from carapi.schemas.car_query import CarFilter
from carapi.services.cars import iter_cars
from tests.conftest import TEST_KEY, make_settings

REPO_SEED = Path(__file__).resolve().parent.parent / "seed" / "cars.csv"
HEADER = "vin,make,model,year,price,mileage,color,status\n"
ROW_A = "1HGCM82633A004352,Honda,Accord,2020,21999.99,35000,Blue,available\n"
ROW_B = "5YJ3E1EA7KF317000,Tesla,Model 3,2022,38900,12000,,reserved\n"
CSV_HDR = {"content-type": "text/csv"}


def post_csv(c: TestClient, body: str | bytes, mode: str | None = None, **headers: str) -> Any:
    params = {"mode": mode} if mode else {}
    return c.post("/cars/import", content=body, params=params, headers=CSV_HDR | headers)


def rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


# ---------- import: happy paths ----------


def test_import_atomic_ok(client: TestClient) -> None:
    r = post_csv(client, HEADER + ROW_A + ROW_B)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "mode": "atomic",
        "rows": 2,
        "created": 2,
        "failed": 0,
        "errors": [],
        "errors_truncated": False,
    }
    cars = client.get("/cars").json()["items"]
    assert [(c["model"], c["price"], c["color"], c["status"]) for c in cars] == [
        ("Accord", 21999.99, "Blue", "available"),
        ("Model 3", 38900.0, None, "reserved"),
    ]


def test_import_header_order_case_bom_crlf_blank_lines(client: TestClient) -> None:
    body = (
        "﻿Mileage, PRICE ,Year,Model,Make,VIN\r\n"
        "\r\n"
        "35000,21999.99,2020,Accord,Honda, 1hgcm82633a004352 \r\n"
        ",,,,,\r\n"
    ).encode()
    r = post_csv(client, body)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1
    car = client.get("/cars").json()["items"][0]
    assert car["vin"] == "1HGCM82633A004352"
    assert car["status"] == "available"  # default when column absent


def test_import_accepts_application_csv_with_charset(client: TestClient) -> None:
    r = post_csv(client, HEADER + ROW_A, **{"content-type": "application/csv; charset=utf-8"})
    assert r.status_code == 200


def test_import_header_only(client: TestClient) -> None:
    assert post_csv(client, HEADER).json()["created"] == 0


# ---------- import: row errors ----------

BAD = (
    HEADER
    + ROW_A  # line 2 ok
    + "BADVIN,Ford,F-150,2013,14000,140000,Black,available\n"  # line 3 vin
    + "1FTFW1ET5DFC00001,Ford,F-150,1800,-5,10,Black,stolen\n"  # line 4 year, price, status
    + "1HGCM82633A004352,Honda,Accord,2020,1,1,,\n"  # line 5 dup in file
    + "JTDKN3DU0A0000001,Toyota,Prius,2020,18999,45000\n"  # line 6 short row
    + ROW_B  # line 7 ok
)


def test_import_atomic_rejects_all(client: TestClient) -> None:
    r = post_csv(client, BAD)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["message"] == "4 of 6 rows invalid; nothing imported"
    report = err["details"]
    assert (report["created"], report["failed"]) == (0, 4)
    by_line = {e["line"]: e for e in report["errors"]}
    assert sorted(by_line) == [3, 4, 5, 6]
    assert by_line[3]["vin"] == "BADVIN"
    assert [f["field"] for f in by_line[3]["errors"]] == ["vin"]
    assert sorted(f["field"] for f in by_line[4]["errors"]) == ["price", "status", "year"]
    assert by_line[5]["errors"][0]["msg"] == "duplicate VIN in file (first on line 2)"
    assert by_line[6]["errors"][0] == {"field": "row", "msg": "expected 8 fields, got 6"}
    assert client.get("/cars").json()["total"] == 0


def test_import_partial_keeps_good_rows(client: TestClient) -> None:
    r = post_csv(client, BAD, mode="partial")
    assert r.status_code == 200
    body = r.json()
    assert (body["mode"], body["rows"], body["created"], body["failed"]) == ("partial", 6, 2, 4)
    assert client.get("/cars").json()["total"] == 2


def test_import_vin_already_in_db(client: TestClient) -> None:
    post_csv(client, HEADER + ROW_A)
    r = post_csv(client, HEADER + ROW_B + ROW_A.lower(), mode="partial")
    body = r.json()
    assert body["created"] == 1
    assert body["errors"] == [
        {
            "line": 3,
            "vin": "1HGCM82633A004352",
            "errors": [{"field": "vin", "msg": "VIN already exists"}],
        }
    ]


def test_import_quoted_fields_and_multiline_line_numbers(client: TestClient) -> None:
    body = (
        HEADER
        + '1HGCM82633A004352,Honda,"Accord, EX",2020,1,1,"Dark\nBlue",available\n'
        + ("BADVIN,Ford,F,2013,1,1,,\n")
    )
    r = post_csv(client, body, mode="partial")
    assert r.json()["created"] == 1
    assert r.json()["errors"][0]["line"] == 4  # physical line, after the 2-line quoted cell
    assert client.get("/cars").json()["items"][0]["model"] == "Accord, EX"


# ---------- import: file-level errors ----------


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("", "File is empty; expected a header row"),
        ("vin,make,model,year,price\n", "Missing required columns: mileage"),
        (HEADER.strip() + ",owner\n", "Unknown columns: owner"),
        ("vin,vin,make,model,year,price,mileage\n", "Duplicate columns: vin"),
        (HEADER + '1HGCM82633A004352,"Hon"da,x,2020,1,1,,\n', "Malformed CSV near line 2"),
    ],
)
def test_import_bad_file(client: TestClient, body: str, message: str) -> None:
    r = post_csv(client, body)
    assert r.status_code == 400
    assert r.json()["error"]["message"].startswith(message)


def test_import_not_utf8(client: TestClient) -> None:
    r = post_csv(client, HEADER.encode() + b"\xff\xfe,bad\n")
    assert r.status_code == 400
    assert "not valid UTF-8" in r.json()["error"]["message"]


def test_import_wrong_content_type(client: TestClient) -> None:
    r = client.post("/cars/import", json={"a": 1})
    assert r.status_code == 415
    assert r.json()["error"]["code"] == "unsupported_media_type"


def test_import_requires_key(app_client: Callable[..., TestClient]) -> None:
    c = app_client()
    assert post_csv(c, HEADER + ROW_A).status_code == 401
    assert post_csv(c, HEADER + ROW_A, **{"X-API-Key": TEST_KEY}).status_code == 200


def test_import_too_many_bytes(app_client: Callable[..., TestClient]) -> None:
    c = app_client(import_max_bytes=60)
    r = post_csv(c, HEADER + ROW_A, **{"X-API-Key": TEST_KEY})
    assert r.status_code == 413
    assert r.json()["error"] == {
        "code": "payload_too_large",
        "message": "File too large (max 60 bytes)",
        "request_id": r.headers["x-request-id"],
    }


def test_import_too_many_rows(app_client: Callable[..., TestClient]) -> None:
    c = app_client(import_max_rows=1)
    r = post_csv(c, HEADER + ROW_A + ROW_B, **{"X-API-Key": TEST_KEY})
    assert r.status_code == 413
    assert r.json()["error"]["message"] == "Too many rows (max 1)"


def test_import_bad_mode(client: TestClient) -> None:
    assert post_csv(client, HEADER, mode="yolo").status_code == 422


# ---------- export ----------


def test_export_empty(client: TestClient) -> None:
    r = client.get("/cars/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/csv; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="cars-')
    assert r.text == ("id,vin,make,model,year,price,mileage,color,status,created_at,updated_at\r\n")


def test_export_rows_and_formatting(client: TestClient) -> None:
    post_csv(client, HEADER + ROW_A + ROW_B)
    out = rows(client.get("/cars/export.csv").text)
    assert [r["vin"] for r in out] == ["1HGCM82633A004352", "5YJ3E1EA7KF317000"]
    assert out[1]["price"] == "38900.00"
    assert out[1]["color"] == ""
    assert out[1]["status"] == "reserved"
    assert out[0]["created_at"].endswith("+00:00")


def test_export_uses_filters_and_sort(client: TestClient) -> None:
    post_csv(client, HEADER + ROW_A + ROW_B)
    out = rows(client.get("/cars/export.csv", params={"sort": "-price"}).text)
    assert [r["make"] for r in out] == ["Tesla", "Honda"]
    out = rows(client.get("/cars/export.csv", params={"make": "honda"}).text)
    assert [r["make"] for r in out] == ["Honda"]


@pytest.mark.parametrize("params", [{"page": 2}, {"size": 5}, {"sort": "vin"}, {"x": 1}])
def test_export_rejects_paging_and_bad_params(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/cars/export.csv", params=params).status_code == 422


def test_export_is_public(app_client: Callable[..., TestClient]) -> None:
    assert app_client().get("/cars/export.csv").status_code == 200


def test_export_route_not_shadowed_by_car_id(client: TestClient) -> None:
    assert client.get("/cars/export.csv").status_code == 200
    assert client.get("/cars/export").status_code == 422  # still goes to /{car_id}


def test_export_escapes_formulas_and_round_trips(client: TestClient) -> None:
    r = client.post(
        "/cars",
        json={
            "vin": "1HGCM82633A004352",
            "make": "=HYPERLINK(1)",
            "model": "+x",
            "year": 2020,
            "price": 1,
            "mileage": 1,
            "color": "@red",
        },
    )
    assert r.status_code == 201
    exported = client.get("/cars/export.csv").text
    row = rows(exported)[0]
    assert (row["make"], row["model"], row["color"]) == ("'=HYPERLINK(1)", "'+x", "'@red")

    with TestClient(create_app(make_settings()), headers={"X-API-Key": TEST_KEY}) as fresh:
        assert post_csv(fresh, exported).json()["created"] == 1
        car = fresh.get("/cars").json()["items"][0]
    assert (car["make"], car["model"], car["color"]) == ("=HYPERLINK(1)", "+x", "@red")


def test_export_import_round_trip_seed(client: TestClient) -> None:
    post_csv(client, REPO_SEED.read_bytes())
    original = client.get("/cars", params={"size": 100}).json()["items"]
    exported = client.get("/cars/export.csv").text
    with TestClient(create_app(make_settings()), headers={"X-API-Key": TEST_KEY}) as fresh:
        assert post_csv(fresh, exported).json()["created"] == len(original)
        copy = fresh.get("/cars", params={"size": 100}).json()["items"]
    keys = ("vin", "make", "model", "year", "price", "mileage", "color", "status")
    assert [{k: c[k] for k in keys} for c in copy] == [{k: c[k] for k in keys} for c in original]


def test_iter_cars_batches(client: TestClient) -> None:
    post_csv(client, REPO_SEED.read_bytes())
    factory = client.app.state.session_factory  # type: ignore[attr-defined]
    with factory() as db:
        sizes = [len(b) for b in iter_cars(db, CarFilter(sort="-price"), batch_size=10)]
        prices = [c.price for b in iter_cars(db, CarFilter(sort="-price"), batch_size=7) for c in b]
    assert sizes == [10, 10, 5]
    assert prices == sorted(prices, reverse=True) and len(prices) == 25


# ---------- seed ----------


def test_repo_seed_file_loads(app_client: Callable[..., TestClient]) -> None:
    c = app_client(seed_file=REPO_SEED)
    body = c.get("/cars").json()
    assert body["total"] == 25
    assert {c["status"] for c in body["items"]} == {"available", "reserved", "sold"}


def test_no_seed_by_default(client: TestClient) -> None:
    assert client.get("/cars").json()["total"] == 0


def test_seed_invalid_fails_startup(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(HEADER + "BADVIN,Ford,F,2013,1,1,,\n")
    app = create_app(make_settings(seed_file=bad))
    with pytest.raises(SeedError, match=r"1 invalid rows: line 2: vin:"), TestClient(app):
        pass


def test_seed_missing_file_fails_startup(tmp_path: Path) -> None:
    app = create_app(make_settings(seed_file=tmp_path / "nope.csv"))
    with pytest.raises(SeedError, match="Cannot read seed file"), TestClient(app):
        pass


def test_seed_file_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CARAPI_SEED_FILE", str(REPO_SEED))
    assert make_settings().seed_file == REPO_SEED
