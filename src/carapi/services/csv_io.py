"""CSV import/export for cars.

Import columns: vin, make, model, year, price, mileage, color, status (any order, case-insensitive).
`id`, `created_at`, `updated_at` are accepted and ignored so an export can be re-imported as-is.
"""

import csv
import io
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from carapi.models.car import Car
from carapi.schemas.car import CarCreate
from carapi.schemas.car_query import CarFilter
from carapi.services.cars import DuplicateVinError, iter_cars

IMPORT_COLUMNS = ("vin", "make", "model", "year", "price", "mileage", "color", "status")
REQUIRED_COLUMNS = frozenset({"vin", "make", "model", "year", "price", "mileage"})
IGNORED_COLUMNS = frozenset({"id", "created_at", "updated_at"})
EXPORT_COLUMNS = ("id", *IMPORT_COLUMNS, "created_at", "updated_at")

# Spreadsheet formula injection: cells starting with these are prefixed with ' on export,
# and the prefix is stripped again on import so export -> import round-trips.
_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")
_TEXT_COLUMNS = ("make", "model", "color")
_VIN_LOOKUP_BATCH = 500
MAX_REPORTED_ERRORS = 1000


class ImportMode(StrEnum):
    ATOMIC = "atomic"  # any bad row -> nothing written
    PARTIAL = "partial"  # write good rows, report bad ones


class CsvFormatError(Exception):
    """The file itself is unusable (encoding, header, malformed CSV)."""


class CsvTooLargeError(Exception):
    pass


@dataclass
class FieldError:
    field: str
    msg: str


@dataclass
class RowError:
    line: int
    vin: str | None
    errors: list[FieldError]


@dataclass
class ImportResult:
    mode: ImportMode
    rows: int = 0
    created: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.errors)


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")  # tolerate Excel's BOM
    except UnicodeDecodeError as exc:
        raise CsvFormatError(f"File is not valid UTF-8 (byte offset {exc.start})") from exc


def _unescape(value: str) -> str:
    if len(value) > 1 and value[0] == "'" and value[1] in _FORMULA_CHARS:
        return value[1:]
    return value


def _escape(value: str) -> str:
    return "'" + value if value.startswith(_FORMULA_CHARS) else value


def _read_header(reader: Iterator[list[str]]) -> list[str]:
    try:
        raw = next(reader)
    except StopIteration:
        raise CsvFormatError("File is empty; expected a header row") from None
    header = [h.strip().lower() for h in raw]
    dupes = sorted({h for h in header if header.count(h) > 1})
    if dupes:
        raise CsvFormatError(f"Duplicate columns: {', '.join(dupes)}")
    unknown = sorted(set(header) - set(IMPORT_COLUMNS) - IGNORED_COLUMNS)
    if unknown:
        raise CsvFormatError(
            f"Unknown columns: {', '.join(unknown)} (allowed: {', '.join(IMPORT_COLUMNS)})"
        )
    missing = sorted(REQUIRED_COLUMNS - set(header))
    if missing:
        raise CsvFormatError(f"Missing required columns: {', '.join(missing)}")
    return header


def parse(
    text: str, max_rows: int | None = None
) -> tuple[list[tuple[int, CarCreate]], ImportResult]:
    """Validate every row. Returns (valid rows with line numbers, result holding row errors)."""
    result = ImportResult(mode=ImportMode.ATOMIC)
    valid: list[tuple[int, CarCreate]] = []
    seen: dict[str, int] = {}
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = _read_header(reader)
        for raw in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in raw):
                continue  # skip blank lines
            result.rows += 1
            if max_rows is not None and result.rows > max_rows:
                raise CsvTooLargeError(f"Too many rows (max {max_rows})")
            if len(raw) != len(header):
                result.errors.append(
                    RowError(
                        line,
                        None,
                        [FieldError("row", f"expected {len(header)} fields, got {len(raw)}")],
                    )
                )
                continue
            data: dict[str, Any] = {}
            for col, cell in zip(header, raw, strict=True):
                value = cell.strip()
                if col in IGNORED_COLUMNS or value == "":
                    continue
                data[col] = _unescape(value) if col in _TEXT_COLUMNS else value
            raw_vin = data.get("vin")
            try:
                car = CarCreate.model_validate(data)
            except ValidationError as exc:
                errs = [
                    FieldError(str(e["loc"][0]) if e["loc"] else "row", e["msg"])
                    for e in exc.errors()
                ]
                result.errors.append(RowError(line, raw_vin, errs))
                continue
            if car.vin in seen:
                result.errors.append(
                    RowError(
                        line,
                        car.vin,
                        [
                            FieldError(
                                "vin", f"duplicate VIN in file (first on line {seen[car.vin]})"
                            )
                        ],
                    )
                )
                continue
            seen[car.vin] = line
            valid.append((line, car))
    except csv.Error as exc:
        raise CsvFormatError(f"Malformed CSV near line {reader.line_num}: {exc}") from exc
    return valid, result


def _existing_vins(db: Session, vins: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(vins), _VIN_LOOKUP_BATCH):
        chunk = vins[i : i + _VIN_LOOKUP_BATCH]
        found.update(db.scalars(select(Car.vin).where(Car.vin.in_(chunk))))
    return found


def import_csv(
    db: Session, text: str, mode: ImportMode, max_rows: int | None = None
) -> ImportResult:
    valid, result = parse(text, max_rows)
    result.mode = mode

    taken = _existing_vins(db, [c.vin for _, c in valid])
    to_insert: list[CarCreate] = []
    for line, car in valid:
        if car.vin in taken:
            result.errors.append(RowError(line, car.vin, [FieldError("vin", "VIN already exists")]))
        else:
            to_insert.append(car)
    result.errors.sort(key=lambda e: e.line)

    if mode is ImportMode.ATOMIC and result.errors:
        return result
    if to_insert:
        db.add_all(Car(**c.model_dump()) for c in to_insert)
        try:
            db.commit()
        except IntegrityError as exc:  # concurrent insert of the same VIN
            db.rollback()
            raise DuplicateVinError("(concurrent import)") from exc
    result.created = len(to_insert)
    return result


def _utc_iso(dt: datetime) -> str:
    return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt).isoformat()


def _row(car: Car) -> list[str]:
    return [
        str(car.id),
        car.vin,
        _escape(car.make),
        _escape(car.model),
        str(car.year),
        f"{car.price:.2f}",
        str(car.mileage),
        _escape(car.color) if car.color else "",
        car.status.value,
        _utc_iso(car.created_at),
        _utc_iso(car.updated_at),
    ]


def export_csv(batches: Iterable[Iterable[Car]]) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")

    def flush() -> str:
        out = buf.getvalue()
        buf.seek(0)
        buf.truncate()
        return out

    writer.writerow(EXPORT_COLUMNS)
    yield flush()
    for batch in batches:
        writer.writerows(_row(c) for c in batch)
        yield flush()


def export_filtered(db: Session, f: CarFilter) -> Iterator[str]:
    return export_csv(iter_cars(db, f))
