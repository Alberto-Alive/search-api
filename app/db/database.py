from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, URL


def create_sqlite_engine(database_path: Path | str) -> Engine:
    path = Path(database_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: Any, _: Any) -> None:
        # Let SQLAlchemy issue BEGIN explicitly so schema DDL and data changes
        # participate in the same transaction on every supported Python version.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys = ON")
        finally:
            cursor.close()

    @event.listens_for(engine, "begin")
    def begin_sqlite_transaction(connection: Any) -> None:
        connection.exec_driver_sql("BEGIN")

    return engine
