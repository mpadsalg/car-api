"""CSV endpoints. Included before the cars router so /cars/export.csv beats /{car_id}."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from carapi.api.errors import error_response
from carapi.core.config import Settings
from carapi.core.security import require_api_key
from carapi.db.session import DbSession
from carapi.schemas.car_query import CarFilter
from carapi.schemas.csv_import import ImportReport
from carapi.services import csv_io
from carapi.services.cars import DuplicateVinError

router = APIRouter(prefix="/cars", tags=["cars"])

CSV_MEDIA_TYPES = frozenset({"text/csv", "application/csv"})

_IMPORT_BODY: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "description": "CSV with header: vin,make,model,year,price,mileage[,color,status]",
        "content": {
            "text/csv": {
                "schema": {"type": "string"},
                "example": "vin,make,model,year,price,mileage,color,status\n"
                "1HGCM82633A004352,Honda,Accord,2003,4999.00,190000,Silver,available\n",
            }
        },
    }
}


async def _read_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File too large (max {limit} bytes)"
        )
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"File too large (max {limit} bytes)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/import",
    response_model=ImportReport,
    dependencies=[Depends(require_api_key)],
    openapi_extra=_IMPORT_BODY,
    responses={
        400: {"description": "Unreadable CSV (encoding/header/format)"},
        401: {"description": "Missing/invalid API key"},
        413: {"description": "File or row count over limit"},
        415: {"description": "Content-Type is not text/csv"},
        422: {"description": "Atomic mode: at least one invalid row, nothing imported"},
    },
)
async def import_cars(
    request: Request,
    db: DbSession,
    mode: Annotated[csv_io.ImportMode, Query()] = csv_io.ImportMode.ATOMIC,
) -> ImportReport | JSONResponse:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in CSV_MEDIA_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Content-Type must be text/csv")
    settings: Settings = request.app.state.settings
    body = await _read_body(request, settings.import_max_bytes)
    try:
        text = csv_io.decode(body)
        result = await run_in_threadpool(
            csv_io.import_csv, db, text, mode, settings.import_max_rows
        )
    except csv_io.CsvFormatError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except csv_io.CsvTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except DuplicateVinError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Import conflicted with a concurrent write"
        ) from exc

    report = ImportReport.from_result(result)
    if mode is csv_io.ImportMode.ATOMIC and result.errors:
        return error_response(
            422,
            f"{result.failed} of {result.rows} rows invalid; nothing imported",
            details=report.model_dump(mode="json"),
        )
    return report


@router.get(
    "/export.csv",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV of all matching cars"}},
)
def export_cars(f: Annotated[CarFilter, Query()], request: Request) -> StreamingResponse:
    factory: sessionmaker[Session] = request.app.state.session_factory

    def body() -> Any:
        # Own session: the request-scoped one may be closed before streaming finishes.
        with factory() as db:
            yield from csv_io.export_filtered(db, f)

    filename = f"cars-{datetime.now(UTC):%Y%m%d}.csv"
    return StreamingResponse(
        body(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
