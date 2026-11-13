from pydantic import BaseModel

from carapi.services.csv_io import MAX_REPORTED_ERRORS, ImportMode, ImportResult


class FieldErrorOut(BaseModel):
    field: str
    msg: str


class RowErrorOut(BaseModel):
    line: int
    vin: str | None
    errors: list[FieldErrorOut]


class ImportReport(BaseModel):
    mode: ImportMode
    rows: int
    created: int
    failed: int
    errors: list[RowErrorOut]
    errors_truncated: bool

    @classmethod
    def from_result(cls, r: ImportResult) -> "ImportReport":
        return cls(
            mode=r.mode,
            rows=r.rows,
            created=r.created,
            failed=r.failed,
            errors=[
                RowErrorOut(
                    line=e.line,
                    vin=e.vin,
                    errors=[FieldErrorOut(field=f.field, msg=f.msg) for f in e.errors],
                )
                for e in r.errors[:MAX_REPORTED_ERRORS]
            ],
            errors_truncated=r.failed > MAX_REPORTED_ERRORS,
        )
