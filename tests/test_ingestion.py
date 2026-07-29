import json
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.main import create_app
from app.services.ingestion import IngestionError

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "database" / "schema.sql"

EXPECTED_TABLE_COUNTS = {
    "countries": 16,
    "categories": 15,
    "distributors": 56,
    "distributor_aliases": 54,
    "distributor_categories": 75,
    "distributor_locations": 67,
    "distributor_contacts": 34,
}

EXPECTED_DATE_STATUSES = {
    "iso": 24,
    "dmy": 6,
    "mdy": 12,
    "ambiguous": 14,
}


def test_initial_import_creates_schema_and_expected_rows(
    database_path: Path,
    source_json_path: Path,
) -> None:
    assert not database_path.exists()
    application = _create_test_app(database_path, source_json_path)

    with TestClient(application):
        summary = application.state.import_summary

    assert database_path.is_file()
    assert summary.schema_created is True
    assert summary.source_records == 56
    assert summary.inserted == 56
    assert summary.updated == 0
    assert summary.removed == 0
    assert summary.table_counts == EXPECTED_TABLE_COUNTS
    assert _table_counts(database_path) == EXPECTED_TABLE_COUNTS


def test_second_import_is_idempotent(
    database_path: Path,
    source_json_path: Path,
) -> None:
    first_application = _create_test_app(database_path, source_json_path)
    with TestClient(first_application):
        first_summary = first_application.state.import_summary
    first_counts = _table_counts(database_path)

    second_application = _create_test_app(database_path, source_json_path)
    with TestClient(second_application):
        second_summary = second_application.state.import_summary
    second_counts = _table_counts(database_path)

    assert first_counts == EXPECTED_TABLE_COUNTS
    assert second_counts == first_counts
    assert first_summary.inserted == 56
    assert second_summary.schema_created is False
    assert second_summary.inserted == 0
    assert second_summary.updated == 56
    assert second_summary.removed == 0


def test_founded_year_normalization_and_raw_json_preservation(
    client: TestClient,
    database_path: Path,
    source_json_path: Path,
) -> None:
    source_records = json.loads(source_json_path.read_text(encoding="utf-8"))
    expected_years = {
        record["id"]: _expected_founded_year(record["founded_year"])
        for record in source_records
    }

    with sqlite3.connect(database_path) as connection:
        stored_rows = connection.execute(
            "SELECT source_id, founded_year, raw_json FROM distributors"
        ).fetchall()

    assert {
        source_id: founded_year
        for source_id, founded_year, _ in stored_rows
    } == expected_years
    assert {
        source_id: json.loads(raw_json)
        for source_id, _, raw_json in stored_rows
    } == {record["id"]: record for record in source_records}


def test_description_normalization(
    client: TestClient,
    database_path: Path,
    source_json_path: Path,
) -> None:
    source_records = json.loads(source_json_path.read_text(encoding="utf-8"))
    expected_descriptions = {
        record["id"]: _expected_description(record.get("description"))
        for record in source_records
    }

    with sqlite3.connect(database_path) as connection:
        actual_descriptions = dict(
            connection.execute(
                "SELECT source_id, description FROM distributors"
            ).fetchall()
        )

    assert actual_descriptions == expected_descriptions


def test_searchable_text_uses_nfkc_whitespace_collapse_and_casefold(
    database_path: Path,
    source_json_path: Path,
) -> None:
    source_records = json.loads(source_json_path.read_text(encoding="utf-8"))
    source_records[0]["name"] = "  Ｐｒｅｍｉｅｒ\tStraße  "
    source_json_path.write_text(
        json.dumps(source_records, ensure_ascii=False),
        encoding="utf-8",
    )

    with TestClient(_create_test_app(database_path, source_json_path)):
        pass

    with sqlite3.connect(database_path) as connection:
        stored_name = connection.execute(
            """
            SELECT name, normalized_name
            FROM distributors
            WHERE source_id = 1
            """
        ).fetchone()

    assert stored_name == ("Premier Straße", "premier strasse")


def test_date_statuses_and_ambiguous_dates(
    client: TestClient,
    database_path: Path,
) -> None:
    with sqlite3.connect(database_path) as connection:
        status_counts = dict(
            connection.execute(
                """
                SELECT last_updated_parse_status, COUNT(*)
                FROM distributors
                GROUP BY last_updated_parse_status
                """
            ).fetchall()
        )
        ambiguous_with_date = connection.execute(
            """
            SELECT COUNT(*)
            FROM distributors
            WHERE last_updated_parse_status = 'ambiguous'
              AND last_updated_date IS NOT NULL
            """
        ).fetchone()[0]

    assert status_counts == EXPECTED_DATE_STATUSES
    assert status_counts["ambiguous"] == 14
    assert ambiguous_with_date == 0


def test_record_17_preserves_both_contact_shapes(
    client: TestClient,
    database_path: Path,
) -> None:
    with sqlite3.connect(database_path) as connection:
        contacts = connection.execute(
            """
            SELECT
                distributor_contacts.source_shape,
                distributor_contacts.email,
                distributor_contacts.phone,
                distributor_contacts.source_position
            FROM distributor_contacts
            JOIN distributors
              ON distributors.id = distributor_contacts.distributor_id
            WHERE distributors.source_id = 17
            ORDER BY distributor_contacts.source_shape
            """
        ).fetchall()

    assert contacts == [
        ("flat_fields", "sales15@example.com", "+2616854671", 0),
        ("nested_contact", "info50@example.com", "+2259976144", 0),
    ]


def test_duplicate_fingerprint_groups_keep_separate_source_records(
    client: TestClient,
    database_path: Path,
) -> None:
    with sqlite3.connect(database_path) as connection:
        duplicate_groups = connection.execute(
            """
            SELECT
                record_fingerprint,
                COUNT(*),
                COUNT(DISTINCT source_id)
            FROM distributors
            GROUP BY record_fingerprint
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        fingerprint_index = next(
            row
            for row in connection.execute(
                "PRAGMA index_list('distributors')"
            ).fetchall()
            if row[1] == "idx_distributors_record_fingerprint"
        )

    assert len(duplicate_groups) == 3
    assert all(row_count == distinct_sources for _, row_count, distinct_sources in duplicate_groups)
    assert all(row_count > 1 for _, row_count, _ in duplicate_groups)
    assert fingerprint_index[2] == 0


def test_foreign_keys_are_enforced(application: FastAPI) -> None:
    with TestClient(application):
        engine = application.state.database_engine
        with engine.begin() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql(
                    """
                    INSERT INTO distributor_aliases (
                        distributor_id,
                        alias,
                        normalized_alias,
                        source_position
                    )
                    VALUES (999999, 'orphan', 'orphan', 0)
                    """
                )


def test_ingestion_failure_rolls_back_complete_import(
    database_path: Path,
    source_json_path: Path,
) -> None:
    with TestClient(_create_test_app(database_path, source_json_path)):
        pass

    before_counts = _table_counts(database_path)
    with sqlite3.connect(database_path) as connection:
        original_name, original_raw_json = connection.execute(
            """
            SELECT name, raw_json
            FROM distributors
            WHERE source_id = 1
            """
        ).fetchone()

    source_records = json.loads(source_json_path.read_text(encoding="utf-8"))
    source_records[0]["name"] = "This update must roll back"
    failing_source_id = source_records[-1]["id"]
    source_records[-1]["founded_year"] = "not-a-year"
    source_json_path.write_text(
        json.dumps(source_records, ensure_ascii=False),
        encoding="utf-8",
    )

    failing_application = _create_test_app(database_path, source_json_path)
    with pytest.raises(
        IngestionError,
        match=rf"source_id={failing_source_id}.*invalid founded_year",
    ):
        with TestClient(failing_application):
            pass

    assert _table_counts(database_path) == before_counts
    with sqlite3.connect(database_path) as connection:
        stored_name, stored_raw_json = connection.execute(
            """
            SELECT name, raw_json
            FROM distributors
            WHERE source_id = 1
            """
        ).fetchone()

    assert stored_name == original_name
    assert stored_raw_json == original_raw_json


def _create_test_app(database_path: Path, source_json_path: Path) -> FastAPI:
    return create_app(
        database_path=database_path,
        data_path=source_json_path,
        schema_path=SCHEMA_PATH,
    )


def _table_counts(database_path: Path) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table_name: connection.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]
            for table_name in EXPECTED_TABLE_COUNTS
        }


def _expected_founded_year(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, str) and value.casefold() == "unknown":
        return None
    return int(value)


def _expected_description(value: str | None) -> str | None:
    if value is None:
        return None
    description = value.strip()
    if not description or description.casefold() in {"n/a", "tbd"}:
        return None
    return description
