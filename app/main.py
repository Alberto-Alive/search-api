import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.db.database import create_sqlite_engine
from app.services.ingestion import initialize_database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "database" / "trendtype.sqlite3"
DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "distributors.json"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
LOGGER = logging.getLogger("uvicorn.error")


def create_app(
    *,
    database_path: Path | str = DEFAULT_DATABASE_PATH,
    data_path: Path | str = DEFAULT_DATA_PATH,
    schema_path: Path | str = DEFAULT_SCHEMA_PATH,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(database_path)
        application.state.database_engine = engine
        try:
            summary = initialize_database(
                engine,
                schema_path=schema_path,
                data_path=data_path,
            )
            application.state.import_summary = summary
            counts = ", ".join(
                f"{table_name}={count}"
                for table_name, count in summary.table_counts.items()
            )
            LOGGER.info(
                "Distributor import complete: schema_created=%s, "
                "source_records=%d, inserted=%d, updated=%d, removed=%d; %s",
                summary.schema_created,
                summary.source_records,
                summary.inserted,
                summary.updated,
                summary.removed,
                counts,
            )
            yield
        finally:
            engine.dispose()

    application = FastAPI(title="Trendtype", lifespan=lifespan)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return application


app = create_app()
