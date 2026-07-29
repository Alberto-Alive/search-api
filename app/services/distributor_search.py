from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine


@dataclass(frozen=True)
class DistributorSearchResult:
    items: list[dict[str, Any]]
    total_items: int
    total_pages: int


ALIASES_QUERY = text(
    """
    SELECT distributor_id, alias
    FROM distributor_aliases
    WHERE distributor_id IN :distributor_ids
    ORDER BY distributor_id, source_position
    """
).bindparams(bindparam("distributor_ids", expanding=True))

CATEGORIES_QUERY = text(
    """
    SELECT
        distributor_categories.distributor_id,
        categories.name
    FROM distributor_categories
    JOIN categories
      ON categories.id = distributor_categories.category_id
    WHERE distributor_categories.distributor_id IN :distributor_ids
    ORDER BY
        distributor_categories.distributor_id,
        categories.normalized_name,
        categories.id
    """
).bindparams(bindparam("distributor_ids", expanding=True))

LOCATIONS_QUERY = text(
    """
    SELECT
        distributor_locations.distributor_id,
        distributor_locations.city,
        countries.name AS country
    FROM distributor_locations
    JOIN countries
      ON countries.id = distributor_locations.country_id
    WHERE distributor_locations.distributor_id IN :distributor_ids
    ORDER BY
        distributor_locations.distributor_id,
        distributor_locations.source_position
    """
).bindparams(bindparam("distributor_ids", expanding=True))

CONTACTS_QUERY = text(
    """
    SELECT
        distributor_id,
        email,
        phone
    FROM distributor_contacts
    WHERE distributor_id IN :distributor_ids
    ORDER BY distributor_id, source_position, source_shape, id
    """
).bindparams(bindparam("distributor_ids", expanding=True))


def search_distributors(
    engine: Engine,
    *,
    name: str | None,
    country: str | None,
    category: str | None,
    page: int,
    page_size: int,
) -> DistributorSearchResult:
    filter_clauses: list[str] = []
    parameters: dict[str, Any] = {}

    if name is not None:
        parameters["name_pattern"] = f"%{_escape_like(name)}%"
        filter_clauses.append(
            """
            (
                distributors.normalized_name
                    LIKE :name_pattern ESCAPE '\\'
                OR EXISTS (
                    SELECT 1
                    FROM distributor_aliases
                    WHERE distributor_aliases.distributor_id = distributors.id
                      AND distributor_aliases.normalized_alias
                          LIKE :name_pattern ESCAPE '\\'
                )
            )
            """
        )

    if country is not None:
        parameters["country"] = country
        filter_clauses.append("countries.normalized_name = :country")

    if category is not None:
        parameters["category"] = category
        filter_clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM distributor_categories
                JOIN categories
                  ON categories.id = distributor_categories.category_id
                WHERE distributor_categories.distributor_id = distributors.id
                  AND categories.normalized_name = :category
            )
            """
        )

    where_clause = (
        "WHERE " + " AND ".join(filter_clauses)
        if filter_clauses
        else ""
    )
    count_query = text(
        f"""
        SELECT COUNT(*)
        FROM distributors
        JOIN countries ON countries.id = distributors.country_id
        {where_clause}
        """
    )

    with engine.connect() as connection:
        total_items = int(
            connection.execute(count_query, parameters).scalar_one()
        )
        total_pages = (
            (total_items + page_size - 1) // page_size
            if total_items
            else 0
        )
        if total_items == 0 or page > total_pages:
            return DistributorSearchResult(
                items=[],
                total_items=total_items,
                total_pages=total_pages,
            )

        page_query = text(
            f"""
            SELECT
                distributors.id AS database_id,
                distributors.source_id,
                distributors.name,
                countries.name AS country,
                distributors.description,
                distributors.founded_year,
                distributors.last_updated_raw,
                distributors.last_updated_date,
                distributors.last_updated_parse_status
            FROM distributors
            JOIN countries ON countries.id = distributors.country_id
            {where_clause}
            ORDER BY distributors.normalized_name, distributors.source_id
            LIMIT :page_size
            OFFSET :offset
            """
        )
        page_parameters = {
            **parameters,
            "page_size": page_size,
            "offset": (page - 1) * page_size,
        }
        distributor_rows = connection.execute(
            page_query,
            page_parameters,
        ).mappings().all()

        items = _build_items(connection, distributor_rows)

    return DistributorSearchResult(
        items=items,
        total_items=total_items,
        total_pages=total_pages,
    )


def _build_items(
    connection: Connection,
    distributor_rows: list[Any],
) -> list[dict[str, Any]]:
    if not distributor_rows:
        return []

    items_by_id: dict[int, dict[str, Any]] = {}
    distributor_ids: list[int] = []

    for row in distributor_rows:
        database_id = int(row["database_id"])
        distributor_ids.append(database_id)
        items_by_id[database_id] = {
            "id": row["source_id"],
            "name": row["name"],
            "aliases": [],
            "country": row["country"],
            "categories": [],
            "description": row["description"],
            "founded_year": row["founded_year"],
            "locations": [],
            "contacts": [],
            "last_updated": {
                "raw": row["last_updated_raw"],
                "date": row["last_updated_date"],
                "parse_status": row["last_updated_parse_status"],
            },
        }

    query_parameters = {"distributor_ids": distributor_ids}

    for row in connection.execute(
        ALIASES_QUERY,
        query_parameters,
    ).mappings():
        items_by_id[row["distributor_id"]]["aliases"].append(row["alias"])

    for row in connection.execute(
        CATEGORIES_QUERY,
        query_parameters,
    ).mappings():
        items_by_id[row["distributor_id"]]["categories"].append(row["name"])

    for row in connection.execute(
        LOCATIONS_QUERY,
        query_parameters,
    ).mappings():
        items_by_id[row["distributor_id"]]["locations"].append(
            {"city": row["city"], "country": row["country"]}
        )

    for row in connection.execute(
        CONTACTS_QUERY,
        query_parameters,
    ).mappings():
        items_by_id[row["distributor_id"]]["contacts"].append(
            {"email": row["email"], "phone": row["phone"]}
        )

    return [items_by_id[database_id] for database_id in distributor_ids]


def _escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
