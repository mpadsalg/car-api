import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from carapi.core.request_context import request_id_var

logger = logging.getLogger("carapi.errors")

_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def error_code(status_code: int) -> str:
    return _CODES.get(status_code) or HTTPStatus(status_code).phrase.lower().replace(" ", "_")


def error_body(status_code: int, message: str, details: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {
        "code": error_code(status_code),
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details is not None:
        err["details"] = details
    return {"error": err}


def error_response(
    status_code: int,
    message: str,
    details: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(error_body(status_code, message, details), status_code, headers=headers)


async def _http_exc(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(exc.status_code, message, headers=dict(exc.headers or {}))


async def _validation_exc(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
        for e in exc.errors()
    ]
    return error_response(422, "Request validation failed", jsonable_encoder(details))


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exc)
    app.add_exception_handler(RequestValidationError, _validation_exc)
