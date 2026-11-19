"""FastAPI app. Run with ONE process:   uvicorn server.app:app --port 8000 --workers 1

Endpoints are plain `def` (not `async def`) on purpose: they do blocking file / network work, and FastAPI
runs plain `def` endpoints in a thread pool so one slow request cannot freeze the whole server.
The heavy work never happens here: POST /process only creates a job and returns immediately.
"""
import json
import threading
from contextlib import asynccontextmanager

import yaml
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config, pipeline, storage
from .catalog import catalog
from .jobs import JobManager


def _load_glaciers() -> dict:
    try:
        cfg = yaml.safe_load(config.GLACIERS_YAML.read_text())
    except FileNotFoundError:
        raise RuntimeError(f"glaciers.yaml not found at {config.GLACIERS_YAML} (set PROJECT_DIR / GLACIERS_YAML)")
    return cfg.get("glaciers", {})


GLACIERS = _load_glaciers()
jobs = JobManager(runner=pipeline.ensure_package)
config.PACKAGES_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background, so the server answers requests immediately:
    threading.Thread(target=catalog.rebuild, args=(list(GLACIERS),), daemon=True, name="catalog-rebuild").start()
    storage.sweep_unsynced(on_synced=catalog.set_synced)      # retry uploads that never finished
    yield


app = FastAPI(title="Open 3D Glacier Change Tracker", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])
app.mount("/files", StaticFiles(directory=config.PACKAGES_DIR), name="files")   # /files/<glacier>/<year>/<file>


def _check(glacier: str, year: int = None) -> None:
    if glacier not in GLACIERS:
        raise HTTPException(404, f"unknown glacier '{glacier}'")
    if year is not None and not (config.MIN_YEAR <= year <= config.MAX_YEAR):
        raise HTTPException(422, f"year must be between {config.MIN_YEAR} and {config.MAX_YEAR}")


@app.get("/api/health")
def health():
    return {"ok": True, "glaciers": len(GLACIERS)}


@app.get("/api/glaciers")
def list_glaciers():
    return [{"id": gid, "name": g.get("name", gid), "rgi_id": g.get("rgi_id"), "bbox": g.get("buffered_bbox_wgs84")}
            for gid, g in GLACIERS.items()]


@app.get("/api/glaciers/{glacier}/years")
def list_years(glacier: str):
    """Every candidate year with a status. 'ready' years carry their headline numbers (for the area chart)."""
    _check(glacier)
    known, active = catalog.years(glacier), jobs.active_years(glacier)
    out = []
    for y in range(config.MIN_YEAR, config.MAX_YEAR + 1):
        if y in known:
            out.append({"year": y, **known[y]})
        else:
            out.append({"year": y, "status": "processing" if y in active else "not_processed"})
    return out


@app.get("/api/glaciers/{glacier}/years/{year}")
def get_year(glacier: str, year: int):
    """meta.json + file URLs, if the package is on this server's disk. Otherwise 404: POST /process loads it."""
    _check(glacier, year)
    if not storage.local_ready(glacier, year):
        raise HTTPException(404, "not available locally; POST .../process to load or compute it")
    meta = json.loads((storage.local_dir(glacier, year) / "meta.json").read_text())
    return {"meta": meta, "files": {f: f"/files/{glacier}/{year}/{f}" for f in storage.FILES}}


@app.post("/api/glaciers/{glacier}/years/{year}/process", status_code=202)
def process(glacier: str, year: int, response: Response):
    """Idempotent: ready -> 200; a job is already running -> that job; otherwise a new job."""
    _check(glacier, year)
    if storage.local_ready(glacier, year):
        response.status_code = 200
        return {"status": "ready"}
    job, created = jobs.submit(glacier, year)
    return {**job.to_dict(), "created": created}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "unknown job (the server may have restarted; POST /process again)")
    return job.to_dict()
