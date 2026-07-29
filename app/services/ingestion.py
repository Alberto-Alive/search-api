from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, Engine

from app.services.normalization import (
    normalize_searchable_text as _searchable_text,
)

REQUIRED_TABLES = {
    "countries",
    "categories",
    "distributors",
    "distributor_aliases",
    "distributor_categories",
    "distributor_locations",
    "distributor_contacts",
}

COUNTED_TABLES = (
    "countries",
    "categories",
    "distributors",
    "distributor_aliases",
    "distributor_categories",
    "distributor_locations",
    "distributor_contacts",
)

ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
SLASH_DATE_PATTERN = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
NUMERIC_YEAR_PATTERN = re.compile(r"[+-]?\d+")


class IngestionError(ValueError):
    """Raised when a source record cannot be imported."""


@dataclass(frozen=True)
class ImportSummary:
    schema_created: bool
    source_records: int
    inserted: int
    updated: int
    removed: int
    table_counts: dict[str, int]


UPSERT_COUNTRY = text(
    """
    INSERT INTO countries (name, normalized_name)
    VALUES (:name, :normalized_name)
    ON CONFLICT (normalized_name) DO UPDATE SET
        name = excluded.name
    RETURNING id
    """
)

UPSERT_CATEGORY = text(
    """
    INSERT INTO categories (name, normalized_name)
    VALUES (:name, :normalized_name)
    ON CONFLICT (normalized_name) DO UPDATE SET
        name = excluded.name
    RETURNING id
    """
)

UPSERT_DISTRIBUTOR = text(
    """
    INSERT INTO distributors (
        source_id,
        name,
        normalized_name,
        country_id,
        description,
        founded_year,
        last_updated_raw,
        last_updated_date,
        last_updated_parse_status,
        record_fingerprint,
        raw_json
    )
    VALUES (
        :source_id,
        :name,
        :normalized_name,
        :country_id,
        :description,
        :founded_year,
        :last_updated_raw,
        :last_updated_date,
        :last_updated_parse_status,
        :record_fingerprint,
        :raw_json
    )
    ON CONFLICT (source_id) DO UPDATE SET
        name = excluded.name,
        normalized_name = excluded.normalized_name,
        country_id = excluded.country_id,
        description = excluded.description,
        founded_year = excluded.founded_year,
        last_updated_raw = excluded.last_updated_raw,
        last_updated_date = excluded.last_updated_date,
        last_updated_parse_status = excluded.last_updated_parse_status,
        record_fingerprint = excluded.record_fingerprint,
        raw_json = excluded.raw_json
    RETURNING id
    """
)

DELETE_MISSING_DISTRIBUTORS = text(
    "DELETE FROM distributors WHERE source_id NOT IN :source_ids"
).bindparams(bindparam("source_ids", expanding=True))


def initialize_database(
    engine: Engine,
    *,
    schema_path: Path | str,
    data_path: Path | str,
) -> ImportSummary:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    records = _load_records(Path(data_path))
    source_ids = _validate_source_ids(records)

    with engine.begin() as connection:
        schema_created = _ensure_schema(connection, schema_sql)
        existing_source_ids = set(
            connection.execute(text("SELECT source_id FROM distributors")).scalars()
        )

        for source_position, (record, source_id) in enumerate(
            zip(records, source_ids, strict=True)
        ):
            try:
                _ingest_record(connection, record, source_id)
            except Exception as exc:
                if isinstance(exc, IngestionError):
                    raise
                raise IngestionError(
                    "Failed to ingest record at source position "
                    f"{source_position} (source_id={source_id}): {exc}"
                ) from exc

        current_source_ids = set(source_ids)
        if source_ids:
            connection.execute(
                DELETE_MISSING_DISTRIBUTORS,
                {"source_ids": tuple(source_ids)},
            )
        else:
            connection.execute(text("DELETE FROM distributors"))

        _remove_orphan_dimensions(connection)
        table_counts = _table_counts(connection)

    return ImportSummary(
        schema_created=schema_created,
        source_records=len(records),
        inserted=len(current_source_ids - existing_source_ids),
        updated=len(current_source_ids & existing_source_ids),
        removed=len(existing_source_ids - current_source_ids),
        table_counts=table_counts,
    )


def _load_records(data_path: Path) -> list[dict[str, Any]]:
    with data_path.open(encoding="utf-8") as source_file:
        records = json.load(source_file)

    if not isinstance(records, list):
        raise IngestionError("The distributor source must contain a JSON array")

    for source_position, record in enumerate(records):
        if not isinstance(record, dict):
            raise IngestionError(
                f"Source position {source_position} must contain a JSON object"
            )

    return records


def _validate_source_ids(records: list[dict[str, Any]]) -> list[int]:
    source_ids: list[int] = []
    seen: set[int] = set()

    for source_position, record in enumerate(records):
        source_id = record.get("id")
        if isinstance(source_id, bool) or not isinstance(source_id, int):
            raise IngestionError(
                f"Source position {source_position} has an invalid integer id"
            )
        if source_id <= 0:
            raise IngestionError(
                f"Source position {source_position} has a non-positive id"
            )
        if source_id in seen:
            raise IngestionError(f"Duplicate source id {source_id}")

        seen.add(source_id)
        source_ids.append(source_id)

    return source_ids


def _ensure_schema(connection: Connection, schema_sql: str) -> bool:
    present_tables = set(
        connection.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        ).scalars()
    )
    present_required_tables = present_tables & REQUIRED_TABLES

    if not present_required_tables:
        _execute_schema(connection, schema_sql)
        created_tables = set(
            connection.execute(
                text(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                    """
                )
            ).scalars()
        )
        missing_tables = REQUIRED_TABLES - created_tables
        if missing_tables:
            raise RuntimeError(
                "Schema initialization did not create required tables: "
                + ", ".join(sorted(missing_tables))
            )
        return True

    missing_tables = REQUIRED_TABLES - present_tables
    if missing_tables:
        raise RuntimeError(
            "Existing database has an incomplete schema; missing tables: "
            + ", ".join(sorted(missing_tables))
        )

    return False


def _execute_schema(connection: Connection, schema_sql: str) -> None:
    statement_lines: list[str] = []

    for line in schema_sql.splitlines(keepends=True):
        statement_lines.append(line)
        candidate = "".join(statement_lines)
        if sqlite3.complete_statement(candidate):
            statement = candidate.strip()
            if statement:
                connection.exec_driver_sql(statement)
            statement_lines.clear()

    if "".join(statement_lines).strip():
        raise RuntimeError("Schema source ends with an incomplete SQL statement")


def _ingest_record(
    connection: Connection,
    record: dict[str, Any],
    source_id: int,
) -> None:
    name, normalized_name = _searchable_text(record.get("name"), "name")
    country_name, normalized_country = _searchable_text(
        record.get("country"), "country"
    )
    country_id = _upsert_country(
        connection,
        country_name,
        normalized_country,
    )
    description = _normalize_description(record.get("description"))
    founded_year = _normalize_founded_year(record.get("founded_year"))
    last_updated_raw = record.get("last_updated")
    last_updated_date, last_updated_status = _parse_last_updated(last_updated_raw)
    raw_json = _canonical_json(record)
    record_fingerprint = _record_fingerprint(record)

    distributor_id = connection.execute(
        UPSERT_DISTRIBUTOR,
        {
            "source_id": source_id,
            "name": name,
            "normalized_name": normalized_name,
            "country_id": country_id,
            "description": description,
            "founded_year": founded_year,
            "last_updated_raw": last_updated_raw,
            "last_updated_date": last_updated_date,
            "last_updated_parse_status": last_updated_status,
            "record_fingerprint": record_fingerprint,
            "raw_json": raw_json,
        },
    ).scalar_one()

    _replace_children(connection, distributor_id, record)


def _replace_children(
    connection: Connection,
    distributor_id: int,
    record: dict[str, Any],
) -> None:
    for table_name in (
        "distributor_aliases",
        "distributor_categories",
        "distributor_locations",
        "distributor_contacts",
    ):
        connection.execute(
            text(f"DELETE FROM {table_name} WHERE distributor_id = :distributor_id"),
            {"distributor_id": distributor_id},
        )

    for source_position, alias_value in enumerate(
        _optional_list(record, "aliases")
    ):
        alias, normalized_alias = _searchable_text(alias_value, "alias")
        connection.execute(
            text(
                """
                INSERT INTO distributor_aliases (
                    distributor_id,
                    alias,
                    normalized_alias,
                    source_position
                )
                VALUES (
                    :distributor_id,
                    :alias,
                    :normalized_alias,
                    :source_position
                )
                """
            ),
            {
                "distributor_id": distributor_id,
                "alias": alias,
                "normalized_alias": normalized_alias,
                "source_position": source_position,
            },
        )

    for category_value in _optional_list(record, "categories"):
        category, normalized_category = _searchable_text(
            category_value, "category"
        )
        category_id = _upsert_category(
            connection,
            category,
            normalized_category,
        )
        connection.execute(
            text(
                """
                INSERT INTO distributor_categories (
                    distributor_id,
                    category_id
                )
                VALUES (:distributor_id, :category_id)
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "distributor_id": distributor_id,
                "category_id": category_id,
            },
        )

    for source_position, location in enumerate(
        _optional_list(record, "locations")
    ):
        if not isinstance(location, dict):
            raise ValueError("location must be an object")
        city, normalized_city = _searchable_text(location.get("city"), "city")
        country, normalized_country = _searchable_text(
            location.get("country"), "location country"
        )
        country_id = _upsert_country(
            connection,
            country,
            normalized_country,
        )
        connection.execute(
            text(
                """
                INSERT INTO distributor_locations (
                    distributor_id,
                    city,
                    normalized_city,
                    country_id,
                    source_position
                )
                VALUES (
                    :distributor_id,
                    :city,
                    :normalized_city,
                    :country_id,
                    :source_position
                )
                """
            ),
            {
                "distributor_id": distributor_id,
                "city": city,
                "normalized_city": normalized_city,
                "country_id": country_id,
                "source_position": source_position,
            },
        )

    if "contact" in record and record["contact"] is not None:
        contact = record["contact"]
        if not isinstance(contact, dict):
            raise ValueError("contact must be an object")
        email, phone = _contact_values(
            contact.get("email"),
            contact.get("phone"),
            "nested contact",
        )
        _insert_contact(
            connection,
            distributor_id,
            email,
            phone,
            "nested_contact",
        )

    if "contact_email" in record or "contact_phone" in record:
        email, phone = _contact_values(
            record.get("contact_email"),
            record.get("contact_phone"),
            "flat contact fields",
        )
        _insert_contact(
            connection,
            distributor_id,
            email,
            phone,
            "flat_fields",
        )


def _upsert_country(
    connection: Connection,
    name: str,
    normalized_name: str,
) -> int:
    return connection.execute(
        UPSERT_COUNTRY,
        {"name": name, "normalized_name": normalized_name},
    ).scalar_one()


def _upsert_category(
    connection: Connection,
    name: str,
    normalized_name: str,
) -> int:
    return connection.execute(
        UPSERT_CATEGORY,
        {"name": name, "normalized_name": normalized_name},
    ).scalar_one()


def _insert_contact(
    connection: Connection,
    distributor_id: int,
    email: str | None,
    phone: str | None,
    source_shape: str,
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO distributor_contacts (
                distributor_id,
                email,
                phone,
                source_shape,
                source_position
            )
            VALUES (
                :distributor_id,
                :email,
                :phone,
                :source_shape,
                0
            )
            """
        ),
        {
            "distributor_id": distributor_id,
            "email": email,
            "phone": phone,
            "source_shape": source_shape,
        },
    )


def _normalize_founded_year(value: Any) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        raise ValueError("founded_year must be an integer, numeric string, or null")

    if isinstance(value, int):
        year = value
    elif isinstance(value, str):
        stripped_value = value.strip()
        if stripped_value.casefold() == "unknown":
            return None
        if not NUMERIC_YEAR_PATTERN.fullmatch(stripped_value):
            raise ValueError(
                f"invalid founded_year {value!r}; expected a numeric string or 'unknown'"
            )
        year = int(stripped_value)
    else:
        raise ValueError("founded_year must be an integer, numeric string, or null")

    if not 1800 <= year <= 2100:
        raise ValueError(f"founded_year {year} is outside the schema range")

    return year


def _normalize_description(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("description must be text or null")

    description = value.strip()
    if not description or description.casefold() in {"n/a", "tbd"}:
        return None

    return description


def _parse_last_updated(value: Any) -> tuple[str | None, str]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("last_updated must be non-blank text")

    date_value = value.strip()
    if ISO_DATE_PATTERN.fullmatch(date_value):
        try:
            return date.fromisoformat(date_value).isoformat(), "iso"
        except ValueError:
            return None, "invalid"

    slash_match = SLASH_DATE_PATTERN.fullmatch(date_value)
    if slash_match is None:
        return None, "invalid"

    first, second, year = (int(part) for part in slash_match.groups())

    if first > 12 and second > 12:
        return None, "invalid"
    if first > 12:
        return _validated_date(year, second, first, "dmy")
    if second > 12:
        return _validated_date(year, first, second, "mdy")

    try:
        date(year, first, second)
        date(year, second, first)
    except ValueError:
        return None, "invalid"

    return None, "ambiguous"


def _validated_date(
    year: int,
    month: int,
    day: int,
    status: str,
) -> tuple[str | None, str]:
    try:
        return date(year, month, day).isoformat(), status
    except ValueError:
        return None, "invalid"


def _contact_values(
    email_value: Any,
    phone_value: Any,
    field_name: str,
) -> tuple[str | None, str | None]:
    email = _optional_trimmed_text(email_value, f"{field_name} email")
    phone = _optional_trimmed_text(phone_value, f"{field_name} phone")
    if email is None and phone is None:
        raise ValueError(f"{field_name} must contain an email or phone")
    return email, phone


def _optional_trimmed_text(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be text or null")
    return value.strip() or None


def _optional_list(record: dict[str, Any], field_name: str) -> list[Any]:
    value = record.get(field_name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array or null")
    return value


def _canonical_json(record: dict[str, Any]) -> str:
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _record_fingerprint(record: dict[str, Any]) -> str:
    fingerprint_source = {
        key: value for key, value in record.items() if key != "id"
    }
    canonical_source = _canonical_json(fingerprint_source)
    return hashlib.sha256(canonical_source.encode("utf-8")).hexdigest()


def _remove_orphan_dimensions(connection: Connection) -> None:
    connection.execute(
        text(
            """
            DELETE FROM categories
            WHERE NOT EXISTS (
                SELECT 1
                FROM distributor_categories
                WHERE distributor_categories.category_id = categories.id
            )
            """
        )
    )
    connection.execute(
        text(
            """
            DELETE FROM countries
            WHERE NOT EXISTS (
                SELECT 1
                FROM distributors
                WHERE distributors.country_id = countries.id
            )
            AND NOT EXISTS (
                SELECT 1
                FROM distributor_locations
                WHERE distributor_locations.country_id = countries.id
            )
            """
        )
    )


def _table_counts(connection: Connection) -> dict[str, int]:
    return {
        table_name: connection.exec_driver_sql(
            f"SELECT COUNT(*) FROM {table_name}"
        ).scalar_one()
        for table_name in COUNTED_TABLES
    }
