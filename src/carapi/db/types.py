from decimal import Decimal
from typing import Any

from sqlalchemy import Dialect, Integer
from sqlalchemy.types import TypeDecorator


class MoneyCents(TypeDecorator[Decimal]):
    """Stores Decimal money as integer cents (exact on SQLite, which lacks native DECIMAL)."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> int | None:
        if value is None:
            return None
        return int((value * 100).to_integral_exact())

    def process_result_value(self, value: Any | None, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        return (Decimal(int(value)) / 100).quantize(Decimal("0.01"))
