from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from carapi.core.security import require_api_key
from carapi.db.session import DbSession
from carapi.models.car import Car
from carapi.schemas.car import CarCreate, CarRead, CarUpdate
from carapi.schemas.car_query import CarQuery
from carapi.schemas.pagination import Page
from carapi.services import cars as service

router = APIRouter(prefix="/cars", tags=["cars"])

_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "Car not found"}}
_CONFLICT: dict[int | str, dict[str, Any]] = {409: {"description": "VIN already exists"}}
_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {401: {"description": "Missing/invalid API key"}}
_WRITE = [Depends(require_api_key)]


def _get_or_404(db: DbSession, car_id: int) -> Car:
    try:
        return service.get_car(db, car_id)
    except service.CarNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post(
    "",
    response_model=CarRead,
    status_code=status.HTTP_201_CREATED,
    responses=_CONFLICT | _UNAUTHORIZED,
    dependencies=_WRITE,
)
def create_car(payload: CarCreate, db: DbSession, response: Response) -> Car:
    try:
        car = service.create_car(db, payload)
    except service.DuplicateVinError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    response.headers["Location"] = f"/cars/{car.id}"
    return car


@router.get("", response_model=Page[CarRead])
def list_cars(query: Annotated[CarQuery, Query()], db: DbSession) -> Page[CarRead]:
    items, total = service.search_cars(db, query)
    return Page[CarRead](
        items=[CarRead.model_validate(c) for c in items],
        total=total,
        page=query.page,
        size=query.size,
        pages=service.page_count(total, query.size),
    )


@router.get("/{car_id}", response_model=CarRead, responses=_NOT_FOUND)
def get_car(car_id: int, db: DbSession) -> Car:
    return _get_or_404(db, car_id)


@router.patch(
    "/{car_id}",
    response_model=CarRead,
    responses=_NOT_FOUND | _CONFLICT | _UNAUTHORIZED,
    dependencies=_WRITE,
)
def update_car(car_id: int, payload: CarUpdate, db: DbSession) -> Car:
    try:
        return service.update_car(db, car_id, payload)
    except service.CarNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except service.DuplicateVinError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.delete(
    "/{car_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND | _UNAUTHORIZED,
    dependencies=_WRITE,
)
def delete_car(car_id: int, db: DbSession) -> None:
    try:
        service.delete_car(db, car_id)
    except service.CarNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
