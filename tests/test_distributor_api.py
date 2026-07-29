import logging
import unicodedata

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

import app.api.distributors as distributor_api

EXPECTED_ITEM_FIELDS = {
    "id",
    "name",
    "aliases",
    "country",
    "categories",
    "description",
    "founded_year",
    "locations",
    "contacts",
    "last_updated",
}


def test_default_pagination_and_sorting(client: TestClient) -> None:
    response = client.get("/api/distributors")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 20
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 20,
        "total_items": 56,
        "total_pages": 3,
    }
    item_sort_keys = [
        (_normalize(item["name"]), item["id"])
        for item in payload["items"]
    ]
    assert item_sort_keys == sorted(item_sort_keys)


def test_name_matches_primary_name_and_alias(client: TestClient) -> None:
    primary_response = client.get(
        "/api/distributors",
        params={"name": "Copperbelt Trading"},
    )
    alias_response = client.get(
        "/api/distributors",
        params={"name": "PCT"},
    )

    assert primary_response.status_code == 200
    assert [item["id"] for item in primary_response.json()["items"]] == [50]
    assert alias_response.status_code == 200
    assert [item["id"] for item in alias_response.json()["items"]] == [12]


def test_filter_case_and_whitespace_normalization(client: TestClient) -> None:
    response = client.get(
        "/api/distributors",
        params={
            "name": "  PREMIER \t DISTRIBUTION  ",
            "country": "  rWaNdA  ",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["total_items"] == 1
    assert [item["id"] for item in payload["items"]] == [1]


def test_country_filter_is_case_insensitive_and_exact(client: TestClient) -> None:
    exact_response = client.get(
        "/api/distributors",
        params={"country": "gHaNa", "page_size": 100},
    )
    partial_response = client.get(
        "/api/distributors",
        params={"country": "Ghan", "page_size": 100},
    )

    assert exact_response.status_code == 200
    assert exact_response.json()["pagination"]["total_items"] > 0
    assert {
        item["country"] for item in exact_response.json()["items"]
    } == {"Ghana"}
    assert partial_response.status_code == 200
    assert partial_response.json()["items"] == []


def test_category_filter_is_case_insensitive_and_exact(client: TestClient) -> None:
    exact_response = client.get(
        "/api/distributors",
        params={"category": "TeXtIlEs", "page_size": 100},
    )
    partial_response = client.get(
        "/api/distributors",
        params={"category": "textile", "page_size": 100},
    )

    assert exact_response.status_code == 200
    assert exact_response.json()["pagination"]["total_items"] > 0
    assert all(
        "textiles" in item["categories"]
        for item in exact_response.json()["items"]
    )
    assert partial_response.status_code == 200
    assert partial_response.json()["items"] == []


def test_filters_combine_with_and(client: TestClient) -> None:
    matching_response = client.get(
        "/api/distributors",
        params={
            "name": "premier",
            "country": "Rwanda",
            "category": "cosmetics",
        },
    )
    mismatched_response = client.get(
        "/api/distributors",
        params={
            "name": "premier",
            "country": "Rwanda",
            "category": "beverages",
        },
    )

    assert matching_response.status_code == 200
    assert [item["id"] for item in matching_response.json()["items"]] == [1]
    assert mismatched_response.status_code == 200
    assert mismatched_response.json()["items"] == []


def test_pages_are_stable_and_non_overlapping(client: TestClient) -> None:
    first_page = client.get(
        "/api/distributors",
        params={"page": 1, "page_size": 7},
    ).json()
    second_page = client.get(
        "/api/distributors",
        params={"page": 2, "page_size": 7},
    ).json()
    repeated_first_page = client.get(
        "/api/distributors",
        params={"page": 1, "page_size": 7},
    ).json()

    first_ids = [item["id"] for item in first_page["items"]]
    second_ids = [item["id"] for item in second_page["items"]]
    assert len(first_ids) == 7
    assert len(second_ids) == 7
    assert set(first_ids).isdisjoint(second_ids)
    assert repeated_first_page["items"] == first_page["items"]


def test_page_beyond_end_returns_empty_items(client: TestClient) -> None:
    response = client.get(
        "/api/distributors",
        params={"page": 999},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {
            "page": 999,
            "page_size": 20,
            "total_items": 56,
            "total_pages": 3,
        },
    }


def test_page_larger_than_sqlite_integer_range_returns_empty_items(
    client: TestClient,
) -> None:
    page = 9223372036854775808

    response = client.get(
        "/api/distributors",
        params={"page": page},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {
            "page": page,
            "page_size": 20,
            "total_items": 56,
            "total_pages": 3,
        },
    }


def test_zero_results_return_200(client: TestClient) -> None:
    response = client.get(
        "/api/distributors",
        params={"name": "definitely-not-a-distributor"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "pagination": {
            "page": 1,
            "page_size": 20,
            "total_items": 0,
            "total_pages": 0,
        },
    }


def test_record_17_returns_both_contacts(client: TestClient) -> None:
    response = client.get(
        "/api/distributors",
        params={
            "name": "Victoria",
            "country": "Uganda",
            "page_size": 100,
        },
    )

    assert response.status_code == 200
    record_17 = next(
        item for item in response.json()["items"] if item["id"] == 17
    )
    assert record_17["contacts"] == [
        {"email": "sales15@example.com", "phone": "+2616854671"},
        {"email": "info50@example.com", "phone": "+2259976144"},
    ]


@pytest.mark.parametrize(
    "literal_search",
    ["%", "_", "\\", "' OR 1=1 --"],
)
def test_name_search_treats_like_and_injection_input_as_literal(
    client: TestClient,
    literal_search: str,
) -> None:
    response = client.get(
        "/api/distributors",
        params={"name": literal_search, "page_size": 100},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["pagination"]["total_items"] == 0


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("page", "0"),
        ("page", "-1"),
        ("page", "not-an-integer"),
        ("page_size", "0"),
        ("page_size", "-1"),
        ("page_size", "101"),
        ("page_size", "not-an-integer"),
    ],
)
def test_invalid_pagination_returns_structured_422(
    client: TestClient,
    parameter: str,
    value: str,
) -> None:
    response = client.get(
        "/api/distributors",
        params={parameter: value},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed"
    assert error["details"][0]["field"] == parameter


@pytest.mark.parametrize("filter_name", ["name", "country", "category"])
@pytest.mark.parametrize("blank_value", ["   ", "\t\n", "\u3000"])
def test_blank_filters_return_structured_422(
    client: TestClient,
    filter_name: str,
    blank_value: str,
) -> None:
    response = client.get(
        "/api/distributors",
        params={filter_name: blank_value},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "details": [
                {
                    "field": filter_name,
                    "message": f"{filter_name} must not be blank",
                    "type": "value_error",
                }
            ],
        }
    }


def test_control_character_filter_returns_structured_422(
    client: TestClient,
) -> None:
    response = client.get("/api/distributors?name=prefix%00suffix")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "details": [
                {
                    "field": "name",
                    "message": "name must not contain control characters",
                    "type": "value_error",
                }
            ],
        }
    }


def test_unknown_query_parameters_return_structured_400(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/distributors",
        params={"county": "Ghana", "pageSize": 10},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "unknown_query_parameter",
            "message": "Unknown query parameter",
            "details": [
                {
                    "field": "county",
                    "message": "Unknown query parameter",
                    "type": "unknown_query_parameter",
                },
                {
                    "field": "pageSize",
                    "message": "Unknown query parameter",
                    "type": "unknown_query_parameter",
                },
            ],
        }
    }


def test_response_structure_uses_public_source_id(client: TestClient) -> None:
    response = client.get(
        "/api/distributors",
        params={
            "name": "Premier Distribution",
            "country": "Rwanda",
        },
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert set(item) == EXPECTED_ITEM_FIELDS
    assert item["id"] == 1
    assert item["name"] == "Premier Distribution"
    assert item["aliases"] == [
        "Premier Distribution",
        "Premier Distribution (PD)",
    ]
    assert item["country"] == "Rwanda"
    assert item["categories"] == [
        "cosmetics",
        "personal care",
        "textiles",
    ]
    assert item["locations"] == [{"city": "Kigali", "country": "Rwanda"}]
    assert item["contacts"] == [
        {"email": "info33@example.com", "phone": "+2551125671"}
    ]
    assert item["last_updated"] == {
        "raw": "25/05/2025",
        "date": "2025-05-25",
        "parse_status": "dmy",
    }
    assert "raw_json" not in item
    assert "record_fingerprint" not in item


def test_openapi_exposes_endpoint_parameters_and_response_model(
    client: TestClient,
) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    operation = response.json()["paths"]["/api/distributors"]["get"]
    parameters = {
        parameter["name"]: parameter for parameter in operation["parameters"]
    }
    assert set(parameters) == {
        "name",
        "country",
        "category",
        "page",
        "page_size",
    }
    assert parameters["page"]["schema"]["default"] == 1
    assert parameters["page"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["schema"]["default"] == 20
    assert parameters["page_size"]["schema"]["minimum"] == 1
    assert parameters["page_size"]["schema"]["maximum"] == 100
    assert operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/DistributorSearchResponse")
    assert operation["responses"]["400"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("/ErrorResponse")


def test_page_loading_uses_fixed_bulk_query_count(
    client: TestClient,
    application: FastAPI,
) -> None:
    statements: list[str] = []

    def capture_statement(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    engine = application.state.database_engine
    event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        response = client.get(
            "/api/distributors",
            params={"page_size": 20},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    assert len(statements) == 6


def test_database_errors_are_logged_and_sanitized(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fail_query(*_args: object, **_kwargs: object) -> None:
        raise OperationalError(
            "SELECT sensitive_column FROM private_table",
            {},
            RuntimeError("sensitive database detail"),
        )

    monkeypatch.setattr(
        distributor_api,
        "search_distributors",
        fail_query,
    )

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        response = client.get("/api/distributors")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An internal server error occurred",
            "details": [],
        }
    }
    assert "sensitive" not in response.text
    assert "Distributor search database query failed" in caplog.text


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
