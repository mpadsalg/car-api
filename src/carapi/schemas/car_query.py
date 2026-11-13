from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from carapi.models.car import CarStatus
from carapi.schemas.car import Color, Mileage, Name, Price, Year

MAX_PAGE_SIZE = 100


class SortField(StrEnum):
    ID = "id"
    MAKE = "make"
    MODEL = "model"
    YEAR = "year"
    PRICE = "price"
    MILEAGE = "mileage"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"


SortKey = tuple[SortField, bool]  # (field, descending)


class CarFilter(BaseModel):
    """Filter + sort params shared by GET /cars and GET /cars/export.csv. Unknown params -> 422."""

    model_config = ConfigDict(extra="forbid")

    q: Annotated[
        str | None,
        Field(min_length=1, max_length=100, description="Substring match on make/model/vin/color"),
    ] = None
    make: Annotated[Name | None, Field(description="Exact, case-insensitive")] = None
    model: Annotated[Name | None, Field(description="Exact, case-insensitive")] = None
    color: Annotated[Color | None, Field(description="Exact, case-insensitive")] = None
    status: list[CarStatus] | None = Field(None, description="Repeatable: ?status=a&status=b")
    year_min: Year | None = None
    year_max: Year | None = None
    price_min: Price | None = None
    price_max: Price | None = None
    mileage_min: Mileage | None = None
    mileage_max: Mileage | None = None
    sort: Annotated[
        str,
        Field(
            description=(
                "Comma-separated fields, '-' prefix for descending, e.g. '-price,year'. "
                f"Fields: {', '.join(f.value for f in SortField)}"
            )
        ),
    ] = "id"

    @field_validator("q")
    @classmethod
    def _strip_q(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("q must not be blank")
        return v

    @field_validator("sort")
    @classmethod
    def _check_sort(cls, v: str) -> str:
        parse_sort(v)
        return v

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        for lo, hi in (
            ("year_min", "year_max"),
            ("price_min", "price_max"),
            ("mileage_min", "mileage_max"),
        ):
            a, b = getattr(self, lo), getattr(self, hi)
            if a is not None and b is not None and a > b:
                raise ValueError(f"{lo} must be <= {hi}")
        return self

    @property
    def sort_keys(self) -> list[SortKey]:
        return parse_sort(self.sort)


class CarQuery(CarFilter):
    """GET /cars: filters + sort + pagination."""

    page: Annotated[int, Field(ge=1)] = 1
    size: Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE)] = 20


def parse_sort(raw: str) -> list[SortKey]:
    keys: list[SortKey] = []
    seen: set[SortField] = set()
    for token in (t.strip() for t in raw.split(",")):
        if not token:
            raise ValueError("sort contains an empty field")
        desc = token.startswith("-")
        name = token.removeprefix("-")
        try:
            field = SortField(name)
        except ValueError:
            allowed = ", ".join(f.value for f in SortField)
            raise ValueError(f"unknown sort field '{name}' (allowed: {allowed})") from None
        if field in seen:
            raise ValueError(f"duplicate sort field '{name}'")
        seen.add(field)
        keys.append((field, desc))
    return keys
