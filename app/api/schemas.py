from datetime import date

from pydantic import BaseModel, Field


class DistributorLocation(BaseModel):
    city: str
    country: str


class DistributorContact(BaseModel):
    email: str | None
    phone: str | None


class LastUpdated(BaseModel):
    raw: str
    date: date | None
    parse_status: str


class DistributorItem(BaseModel):
    id: int
    name: str
    aliases: list[str]
    country: str
    categories: list[str]
    description: str | None
    founded_year: int | None
    locations: list[DistributorLocation]
    contacts: list[DistributorContact]
    last_updated: LastUpdated


class Pagination(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int


class DistributorSearchResponse(BaseModel):
    items: list[DistributorItem]
    pagination: Pagination


class ErrorDetail(BaseModel):
    field: str | None = None
    message: str
    type: str | None = None


class ErrorContent(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorContent
