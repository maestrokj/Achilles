"""RFC 9457 problem+json — the single error envelope for every router.

Shape: ``{type, title, status, detail, code, request_id}`` (+ ``errors[]`` on 422,
``retry_after`` on 429). Module-specific codes live next to their module
(proximity principle); this layer owns only the generic ones.
Design: auth-security/_workzone/data-model.html#api-errors.
"""

import logging
from http import HTTPStatus
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import DBAPIError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"

CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_RATE_LIMITED = "RATE_LIMITED"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFLICT = "CONFLICT"
CODE_FORBIDDEN = "FORBIDDEN"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"

# Statuses whose code differs from the derived reason phrase (404 → NOT_FOUND is automatic).
_STATUS_CODES = {
    422: CODE_VALIDATION_ERROR,
    429: CODE_RATE_LIMITED,
}


class ApiError(Exception):
    """Raise anywhere below a router; the installed handler renders problem+json."""

    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str = "",
        *,
        errors: list[dict[str, str]] | None = None,
        retry_after: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.errors = errors
        self.retry_after = retry_after
        self.headers = headers


def problem_type(code: str) -> str:
    """Derive the RFC `type` URI from the machine code: FOO_BAR → /errors/foo-bar."""
    return "/errors/" + code.lower().replace("_", "-")


def rate_limited(retry_after: int, detail: str = "Too many requests") -> ApiError:
    """The one 429 envelope: Retry-After is always present and at least 1s."""
    return ApiError(
        429,
        CODE_RATE_LIMITED,
        "Rate limited",
        detail,
        retry_after=max(retry_after, 1),
    )


def field_validation_error(field: str, message: str, *, detail: str | None = None) -> ApiError:
    """The one single-field 422 envelope: ``errors`` names the field the client marks."""
    return ApiError(
        422,
        CODE_VALIDATION_ERROR,
        "Validation error",
        detail if detail is not None else message,
        errors=[{"field": field, "message": message}],
    )


def problem_response(
    request_id: str,
    *,
    status: int,
    code: str,
    title: str,
    detail: str = "",
    errors: list[dict[str, str]] | None = None,
    retry_after: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": problem_type(code),
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": request_id,
    }
    if errors is not None:
        body["errors"] = errors
    all_headers = dict(headers or {})
    if retry_after is not None:
        body["retry_after"] = retry_after
        all_headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        body,
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=all_headers,
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def _validation_errors(exc: RequestValidationError) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ())]
        # Drop the container segment ("body" / "query" / "path") — clients match on the field.
        field = ".".join(loc[1:] if len(loc) > 1 else loc)
        items.append({"field": field, "message": str(err.get("msg", ""))})
    return items


async def _handle_api_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)  # noqa: S101 — registered for ApiError only
    return problem_response(
        _request_id(request),
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        errors=exc.errors,
        retry_after=exc.retry_after,
        headers=exc.headers,
    )


async def _handle_validation(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    return problem_response(
        _request_id(request),
        status=422,
        code=CODE_VALIDATION_ERROR,
        title="Validation error",
        detail="Request body or parameters failed validation",
        errors=_validation_errors(exc),
    )


async def _handle_http(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    phrase = HTTPStatus(exc.status_code).phrase
    code = _STATUS_CODES.get(exc.status_code, phrase.upper().replace(" ", "_"))
    return problem_response(
        _request_id(request),
        status=exc.status_code,
        code=code,
        title=phrase,
        detail=str(exc.detail) if exc.detail else "",
        headers=dict(exc.headers) if exc.headers else None,
    )


def _is_bad_input_error(exc: BaseException) -> bool:
    # asyncpg raises DataError when a value can't be represented — an id past
    # int64, a number past a column's range, a bad literal. SQLAlchemy wraps it in
    # the generic DBAPIError (no SQLSTATE, it fails at bind time), so we walk the
    # cause chain. Operational/connection faults stay 500.
    seen = exc
    while seen is not None:
        if isinstance(seen, asyncpg.exceptions.DataError):
            return True
        seen = seen.__cause__
    return False


async def _handle_db_error(request: Request, exc: Exception) -> JSONResponse:
    if not _is_bad_input_error(exc):
        return await _handle_unhandled(request, exc)
    # Bad *client* input, not a server fault — the same 422 a bounded field gives.
    logger.warning("Bad-input DB error on %s %s: %s", request.method, request.url.path, exc)
    return problem_response(
        _request_id(request),
        status=422,
        code=CODE_VALIDATION_ERROR,
        title="Validation error",
        detail="A parameter value is out of the accepted range",
    )


async def _handle_unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return problem_response(
        _request_id(request),
        status=500,
        code=CODE_INTERNAL_ERROR,
        title="Internal Server Error",
        detail="An unexpected error occurred",
    )


def install_problem_handlers(app: FastAPI) -> None:
    """Route every refusal shape through the one envelope — never the framework default."""
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation)
    app.add_exception_handler(StarletteHTTPException, _handle_http)
    app.add_exception_handler(DBAPIError, _handle_db_error)
    app.add_exception_handler(Exception, _handle_unhandled)
