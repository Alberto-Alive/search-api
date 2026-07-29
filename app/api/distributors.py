import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import APIError
from app.api.schemas import (
    DistributorSearchResponse,
    ErrorDetail,
    ErrorResponse,
    Pagination,
)
from app.services.distributor_search import search_distributors
from app.services.normalization import normalize_filter_value

LOGGER = logging.getLogger("uvicorn.error")
ALLOWED_QUERY_PARAMETERS = frozenset(
    {"name", "country", "category", "page", "page_size"}
)

router = APIRouter(prefix="/api", tags=["distributors"])


def _database_engine(request: Request) -> Engine:
    return request.app.state.database_engine


def _reject_unknown_query_parameters(request: Request) -> None:
    unknown_parameters = sorted(
        set(request.query_params.keys()) - ALLOWED_QUERY_PARAMETERS
    )
    if not unknown_parameters:
        return

    raise APIError(
        status_code=400,
        code="unknown_query_parameter",
        message="Unknown query parameter",
        details=[
            ErrorDetail(
                field=parameter,
                message="Unknown query parameter",
                type="unknown_query_parameter",
            )
            for parameter in unknown_parameters
        ],
    )


@router.get(
    "/distributors",
    response_model=DistributorSearchResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(_reject_unknown_query_parameters)],
)
def get_distributors(
    engine: Annotated[Engine, Depends(_database_engine)],
    name: Annotated[str | None, Query()] = None,
    country: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DistributorSearchResponse:
    normalized_filters: dict[str, str | None] = {}
    for field_name, value in (
        ("name", name),
        ("country", country),
        ("category", category),
    ):
        if value is None:
            normalized_filters[field_name] = None
            continue
        try:
            normalized_filters[field_name] = normalize_filter_value(
                value,
                field_name,
            )
        except ValueError as exc:
            raise APIError(
                status_code=422,
                code="validation_error",
                message="Request validation failed",
                details=[
                    ErrorDetail(
                        field=field_name,
                        message=str(exc),
                        type="value_error",
                    )
                ],
            ) from None

    try:
        result = search_distributors(
            engine,
            name=normalized_filters["name"],
            country=normalized_filters["country"],
            category=normalized_filters["category"],
            page=page,
            page_size=page_size,
        )
    except SQLAlchemyError:
        LOGGER.exception("Distributor search database query failed")
        raise APIError(
            status_code=500,
            code="internal_server_error",
            message="An internal server error occurred",
        ) from None

    return DistributorSearchResponse(
        items=result.items,
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total_items=result.total_items,
            total_pages=result.total_pages,
        ),
    )
