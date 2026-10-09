"""Structured error envelope shared by every endpoint.

Every non-2xx response has the shape::

    {"error": {"code": "provider_unavailable", "message": "...",
               "hint": "...", "retryable": true, "request_id": "..."}}

`code` is stable and machine-readable; `hint` tells an operator what to do.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger("errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        details: dict[str, Any] | None = None,
        code: str | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.details = details or {}
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "request_id": request_id,
        }
        if self.hint:
            body["hint"] = self.hint
        if self.details:
            body["details"] = self.details
        return body


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ValidationFailed(AppError):
    status_code = 422
    code = "validation_error"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ProviderUnavailable(AppError):
    status_code = 503
    code = "provider_unavailable"
    retryable = True


class ModelTimeout(AppError):
    status_code = 504
    code = "model_timeout"
    retryable = True


class ModelError(AppError):
    status_code = 502
    code = "model_error"
    retryable = True


class DatabaseUnavailable(AppError):
    status_code = 503
    code = "database_unavailable"
    retryable = True


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _envelope(status: int, body: dict[str, Any]) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": body})


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        log.warning("app_error", code=exc.code, message=exc.message, status=exc.status_code)
        return _envelope(exc.status_code, exc.to_dict(_request_id(request)))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        issues = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        err = ValidationFailed("Request validation failed.", details={"issues": issues})
        return _envelope(422, err.to_dict(_request_id(request)))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        err = AppError(str(exc.detail), code=code, status_code=exc.status_code)
        return _envelope(exc.status_code, err.to_dict(_request_id(request)))

    @app.exception_handler(OperationalError)
    @app.exception_handler(DBAPIError)
    @app.exception_handler(OSError)
    async def _db(request: Request, exc: Exception) -> JSONResponse:
        log.error("database_error", error=str(exc)[:500])
        err = DatabaseUnavailable(
            "The database is unreachable.",
            hint="Check that PostgreSQL is running and DATABASE_URL is correct (GET /health/ready).",
        )
        return _envelope(503, err.to_dict(_request_id(request)))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error")
        err = AppError("Unexpected server error.", hint="See server logs with this request_id.")
        return _envelope(500, err.to_dict(_request_id(request)))
