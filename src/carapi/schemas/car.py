from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from carapi.models.car import CarStatus

MIN_YEAR = 1886  # first production automobile


def _check_year(v: int) -> int:
    max_year = date.today().year + 1
    if not MIN_YEAR <= v <= max_year:
        raise ValueError(f"year must be between {MIN_YEAR} and {max_year}")
    return v


# 17 chars, no I/O/Q (ISO 3779); normalised to upper case.
def _normalize_vin(v: object) -> object:
    return v.strip().upper() if isinstance(v, str) else v


Vin = Annotated[
    str,
    BeforeValidator(_normalize_vin),
    StringConstraints(pattern=r"^[A-HJ-NPR-Z0-9]{17}$"),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Color = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
Year = Annotated[int, AfterValidator(_check_year)]
Price = Annotated[
    Decimal,
    Field(ge=0, max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
]


def _as_utc(v: datetime) -> datetime:
    # SQLite drops tzinfo; values are always written in UTC.
    return v.replace(tzinfo=UTC) if v.tzinfo is None else v


UtcDatetime = Annotated[datetime, AfterValidator(_as_utc)]
Mileage = Annotated[int, Field(ge=0, le=5_000_000)]


class CarCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vin: Vin
    make: Name
    model: Name
    year: Year
    price: Price
    mileage: Mileage
    color: Color | None = None
    status: CarStatus = CarStatus.AVAILABLE


class CarUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vin: Vin | None = None
    make: Name | None = None
    model: Name | None = None
    year: Year | None = None
    price: Price | None = None
    mileage: Mileage | None = None
    color: Color | None = None
    status: CarStatus | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        nullable = {"color"}
        bad = sorted(f for f in self.model_fields_set - nullable if getattr(self, f) is None)
        if bad:
            raise ValueError(f"fields cannot be null: {', '.join(bad)}")
        return self


class CarRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vin: str
    make: str
    model: str
    year: int
    price: Price
    mileage: int
    color: str | None
    status: CarStatus
    created_at: UtcDatetime
    updated_at: UtcDatetime
