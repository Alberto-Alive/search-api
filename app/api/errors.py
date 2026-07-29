from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas import ErrorContent, ErrorDetail, ErrorResponse


class APIError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def register_error_handlers(application: FastAPI) -> None:
    application.add_exception_handler(APIError, _api_error_handler)
    application.add_exception_handler(
        RequestValidationError,
        _request_validation_error_handler,
    )


async def _api_error_handler(_: Request, exception: Exception) -> JSONResponse:
    api_error = exception
    if not isinstance(api_error, APIError):
        raise api_error

    return _error_response(
        status_code=api_error.status_code,
        code=api_error.code,
        message=api_error.message,
        details=api_error.details,
    )


async def _request_validation_error_handler(
    _: Request,
    exception: Exception,
) -> JSONResponse:
    validation_error = exception
    if not isinstance(validation_error, RequestValidationError):
        raise validation_error

    details = [
        ErrorDetail(
            field=".".join(str(part) for part in error["loc"][1:]) or None,
            message=error["msg"],
            type=error["type"],
        )
        for error in validation_error.errors()
    ]
    return _error_response(
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=details,
    )


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail],
) -> JSONResponse:
    response = ErrorResponse(
        error=ErrorContent(
            code=code,
            message=message,
            details=details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json", exclude_none=True),
    )
