from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

app = FastAPI(title="Trendtype")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
