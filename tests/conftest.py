from collections.abc import Iterator
from pathlib import Path
from shutil import copyfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import create_app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA_PATH = PROJECT_ROOT / "data" / "distributors.json"
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "trendtype.sqlite3"


@pytest.fixture
def source_json_path(tmp_path: Path) -> Path:
    copied_source_path = tmp_path / "distributors.json"
    copyfile(SOURCE_DATA_PATH, copied_source_path)
    return copied_source_path


@pytest.fixture
def application(database_path: Path, source_json_path: Path) -> FastAPI:
    return create_app(
        database_path=database_path,
        data_path=source_json_path,
        schema_path=SCHEMA_PATH,
    )


@pytest.fixture
def client(application: FastAPI) -> Iterator[TestClient]:
    with TestClient(application) as test_client:
        yield test_client
