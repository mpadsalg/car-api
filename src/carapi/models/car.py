from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from carapi.db.base import Base
from carapi.db.types import MoneyCents


class CarStatus(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    SOLD = "sold"


def _now() -> datetime:
    return datetime.now(UTC)


class Car(Base):
    __tablename__ = "cars"
    __table_args__ = (
        # Case-insensitive equality filters on make/model use lower().
        Index("ix_cars_make_lower", func.lower("make")),
        Index("ix_cars_model_lower", func.lower("model")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vin: Mapped[str] = mapped_column(String(17), unique=True, index=True)
    make: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(50))
    year: Mapped[int] = mapped_column(Integer, index=True)
    price: Mapped[Decimal] = mapped_column(MoneyCents, index=True)
    mileage: Mapped[int] = mapped_column(Integer, index=True)
    color: Mapped[str | None] = mapped_column(String(30), default=None)
    status: Mapped[CarStatus] = mapped_column(
        Enum(
            CarStatus, native_enum=False, length=10, values_callable=lambda e: [m.value for m in e]
        ),
        default=CarStatus.AVAILABLE,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
