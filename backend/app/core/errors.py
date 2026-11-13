"""One error format for the whole API (Technical Plan §4.1).

Every error - ours, FastAPI's validation errors, 404s, database outages,
unexpected crashes - is returned as:

    { "error": { "code": "SOME_CODE", "message": "Human readable", "details": {...} } }

The code is stable, so the website and the mobile app can both map it to the
same friendly message. Internal details (stack traces, SQL) are NEVER sent to
the client; they are written to the server log instead.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("freshlink")


class AppError(Exception):
    """Raise this from services/routers for expected business errors.

    Example: raise AppError(409, "LISTING_QUANTITY_EXCEEDED", "Only 5 packages remain")
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def error_response(
    status_code: int, code: str, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


# Standard HTTP errors raised by FastAPI/Starlette themselves -> stable codes.
_HTTP_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    response = error_response(exc.status_code, exc.code, exc.message, exc.details)
    response.headers.update(exc.headers)
    return response


async def _http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").capitalize()
    response = error_response(exc.status_code, code, message)
    if exc.headers:  # e.g. "Allow" for 405, "WWW-Authenticate" for 401
        response.headers.update(exc.headers)
    return response


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Turn FastAPI's list of problems into {"fields": [{"field": "body.quantity", "message": ...}]}
    fields = [
        {
            "field": ".".join(str(part) for part in err.get("loc", ())),
            "message": err.get("msg", "Invalid value"),
            "type": err.get("type", ""),
        }
        for err in exc.errors()
    ]
    return error_response(
        422, "VALIDATION_ERROR", "Some fields are missing or invalid.", {"fields": fields}
    )


async def _database_unavailable_handler(request: Request, exc: OperationalError) -> JSONResponse:
    logger.error("Database unavailable: %s", exc)
    return error_response(
        503, "DATABASE_UNAVAILABLE", "The service is temporarily unavailable. Please try again."
    )


async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return error_response(500, "INTERNAL_ERROR", "Something went wrong on our side.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(OperationalError, _database_unavailable_handler)
    app.add_exception_handler(Exception, _unexpected_error_handler)
