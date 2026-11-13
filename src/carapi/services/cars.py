import math
from collections.abc import Iterator, Sequence
from typing import Any

from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from carapi.models.car import Car
from carapi.schemas.car import CarCreate, CarUpdate
from carapi.schemas.car_query import CarFilter, CarQuery


class CarNotFoundError(Exception):
    def __init__(self, car_id: int) -> None:
        super().__init__(f"Car {car_id} not found")
        self.car_id = car_id


class DuplicateVinError(Exception):
    def __init__(self, vin: str) -> None:
        super().__init__(f"Car with VIN {vin} already exists")
        self.vin = vin


def _vin_taken(db: Session, vin: str, exclude_id: int | None = None) -> bool:
    stmt = select(Car.id).where(Car.vin == vin)
    if exclude_id is not None:
        stmt = stmt.where(Car.id != exclude_id)
    return db.scalar(stmt) is not None


def _commit(db: Session, vin: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:  # race between check and insert
        db.rollback()
        raise DuplicateVinError(vin) from exc


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filters(q: CarFilter) -> list[ColumnElement[bool]]:
    conds: list[ColumnElement[bool]] = []
    if q.q:
        pattern = f"%{_escape_like(q.q.lower())}%"
        conds.append(
            or_(
                *(
                    func.lower(col).like(pattern, escape="\\")
                    for col in (Car.make, Car.model, Car.vin, Car.color)
                )
            )
        )
    for col, val in ((Car.make, q.make), (Car.model, q.model), (Car.color, q.color)):
        if val is not None:
            conds.append(func.lower(col) == val.lower())
    if q.status:
        conds.append(Car.status.in_(q.status))
    ranges: tuple[tuple[Any, Any, Any], ...] = (
        (Car.year, q.year_min, q.year_max),
        (Car.price, q.price_min, q.price_max),
        (Car.mileage, q.mileage_min, q.mileage_max),
    )
    for col, lo, hi in ranges:
        if lo is not None:
            conds.append(col >= lo)
        if hi is not None:
            conds.append(col <= hi)
    return conds


def _order_by(q: CarFilter) -> list[Any]:
    clauses: list[Any] = []
    has_id = False
    for field, desc in q.sort_keys:
        col = getattr(Car, field.value)
        clauses.append(col.desc() if desc else col.asc())
        has_id |= field.value == "id"
    if not has_id:
        clauses.append(Car.id.asc())  # deterministic tie-breaker for stable pages
    return clauses


def search_cars(db: Session, q: CarQuery) -> tuple[Sequence[Car], int]:
    conds = _filters(q)
    total = db.scalar(select(func.count()).select_from(Car).where(*conds)) or 0
    stmt: Select[Car] = (
        select(Car)
        .where(*conds)
        .order_by(*_order_by(q))
        .limit(q.size)
        .offset((q.page - 1) * q.size)
    )
    return db.scalars(stmt).all(), total


def iter_cars(db: Session, f: CarFilter, batch_size: int = 500) -> Iterator[Sequence[Car]]:
    """Yield all matching cars in batches (stable order thanks to the id tie-breaker)."""
    stmt = select(Car).where(*_filters(f)).order_by(*_order_by(f))
    offset = 0
    while True:
        batch = db.scalars(stmt.limit(batch_size).offset(offset)).all()
        if not batch:
            return
        yield batch
        if len(batch) < batch_size:
            return
        offset += batch_size


def page_count(total: int, size: int) -> int:
    return math.ceil(total / size) if total else 0


def get_car(db: Session, car_id: int) -> Car:
    car = db.get(Car, car_id)
    if car is None:
        raise CarNotFoundError(car_id)
    return car


def create_car(db: Session, data: CarCreate) -> Car:
    if _vin_taken(db, data.vin):
        raise DuplicateVinError(data.vin)
    car = Car(**data.model_dump())
    db.add(car)
    _commit(db, data.vin)
    db.refresh(car)
    return car


def update_car(db: Session, car_id: int, data: CarUpdate) -> Car:
    car = get_car(db, car_id)
    changes = data.model_dump(exclude_unset=True)
    if "vin" in changes and _vin_taken(db, changes["vin"], exclude_id=car_id):
        raise DuplicateVinError(changes["vin"])
    for field, value in changes.items():
        setattr(car, field, value)
    _commit(db, car.vin)
    db.refresh(car)
    return car


def delete_car(db: Session, car_id: int) -> None:
    car = get_car(db, car_id)
    db.delete(car)
    db.commit()
