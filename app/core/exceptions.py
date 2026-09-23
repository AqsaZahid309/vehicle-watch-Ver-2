import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base application error with HTTP semantics."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class NotFoundError(AppError):
    def __init__(self, resource: str, resource_id: str = "") -> None:
        msg = f"{resource} not found" if not resource_id else f"{resource} '{resource_id}' not found"
        super().__init__(msg, status.HTTP_404_NOT_FOUND)


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Not authenticated") -> None:
        super().__init__(message, status.HTTP_401_UNAUTHORIZED)


class ForbiddenError(AppError):
    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message, status.HTTP_403_FORBIDDEN)


class ConflictError(AppError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status.HTTP_409_CONFLICT)


class RateLimitError(AppError):
    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__("Rate limit exceeded", status.HTTP_429_TOO_MANY_REQUESTS)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message},
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic v2 errors() can include a `ctx` dict containing a raw Exception object.
        # Sanitize to string so the response is always JSON-serializable.
        def _sanitize(errors: list) -> list:
            clean = []
            for err in errors:
                e = dict(err)
                if "ctx" in e and isinstance(e["ctx"], dict):
                    e["ctx"] = {
                        k: str(v) if isinstance(v, Exception) else v
                        for k, v in e["ctx"].items()
                    }
                clean.append(e)
            return clean

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Validation error",
                "errors": _sanitize(exc.errors()),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "An unexpected error occurred"},
        )
