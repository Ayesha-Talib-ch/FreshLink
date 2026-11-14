"""One error format for the whole API (Technical Plan section 4.1).

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
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


# ----------------------------------------------- the documented error shape


class ErrorBody(BaseModel):
    code: str = Field(examples=["VALIDATION_ERROR"])
    message: str = Field(examples=["Some fields are missing or invalid."])
    details: dict[str, Any] = Field(
        default_factory=dict,
        examples=[{"fields": [{"field": "body.phone", "message": "Enter a valid phone number.", "type": "value_error"}]}],
    )


class ErrorResponse(BaseModel):
    """Shape of EVERY error response. Shown in Swagger so both apps can rely on it."""

    error: ErrorBody


_DESCRIPTIONS = {
    400: "Bad request (e.g. wrong current password)",
    401: "Not logged in, or the session/token is invalid or expired",
    403: "Logged in, but not allowed (wrong role, not verified, suspended ...)",
    404: "Not found (or not visible to you)",
    409: "Conflict (duplicate, or the data changed at the same time)",
    413: "File too large",
    415: "Unsupported file type (only PDF, JPG, PNG)",
    422: "Validation error: details.fields lists each problem",
    429: "Too many attempts; see the Retry-After header",
    503: "Database temporarily unavailable",
}


def error_responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """For @router.get(..., responses=error_responses(404, 409)): documents error codes."""
    return {code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in codes}


# ------------------------------------------------------------------ handlers


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

# Pydantic adds these technical prefixes to messages from our own validators,
# e.g. "Value error, Password must be at least 8 characters long."
_PYDANTIC_PREFIXES = ("Value error, ", "Assertion failed, ")


def clean_message(message: str) -> str:
    for prefix in _PYDANTIC_PREFIXES:
        if message.startswith(prefix):
            return message[len(prefix):]
    return message


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
            "message": clean_message(err.get("msg", "Invalid value")),
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


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(OperationalError, _database_unavailable_handler)


class CatchUnexpectedErrorsMiddleware:
    """Turns any crash into a clean 500 INTERNAL_ERROR response.

    Why a middleware and not app.add_exception_handler(Exception, ...)? FastAPI
    runs the catch-all Exception handler OUTSIDE the CORS middleware, so the 500
    response had no Access-Control-Allow-Origin header. The browser then hid our
    message and the website could only show a confusing "CORS error". Installed
    INSIDE the CORS middleware (see main.py), the 500 response gets CORS headers too.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_and_track(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_and_track)
        except Exception:
            if response_started:  # too late to replace the response; let the server handle it
                raise
            logger.exception("Unhandled error on %s %s", scope.get("method"), scope.get("path"))
            response = error_response(500, "INTERNAL_ERROR", "Something went wrong on our side.")
            await response(scope, receive, send)
