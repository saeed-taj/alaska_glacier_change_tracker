"""catalog.json: a small, REBUILDABLE index of which glacier-years exist and their headline numbers.

It is a cache, not a source of truth. The truth is the packages (meta.json) on disk and in the bucket,
so rebuild() can always regenerate this file. That is also why "processing" is NOT stored here:
a status persisted in a file gets stuck forever when the server dies mid-job. Live job state lives
in JobManager (memory) and disappears with the process, which is what you want.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Iterable

from . import config, storage

log = logging.getLogger("catalog")


def summarize(meta: dict, synced: bool) -> dict:
    r = meta.get("results") or meta            # new schema nests numbers under "results"; old files were flat
    return {
        "status": "ready",
        "area_km2_predicted": r.get("area_km2_predicted"),
        "area_km2_rgi": r.get("area_km2_rgi"),
        "processed_at": (meta.get("processing") or {}).get("processed_at"),
        "synced": synced,                      # True once the bucket has the package
    }


class Catalog:
    def __init__(self, path):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data: dict = {}
        try:
            self._data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save_locked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        os.replace(tmp, self._path)            # atomic: readers see the old file or the new one, never half

    def set_ready(self, glacier: str, year: int, meta: dict, synced: bool = False) -> None:
        with self._lock:
            self._data.setdefault(glacier, {})[str(year)] = summarize(meta, synced)
            self._save_locked()

    def set_synced(self, glacier: str, year: int) -> None:
        with self._lock:
            entry = self._data.get(glacier, {}).get(str(year))
            if entry is not None:
                entry["synced"] = True
                self._save_locked()

    def years(self, glacier: str) -> dict:
        with self._lock:
            return {int(y): dict(e) for y, e in self._data.get(glacier, {}).items()}

    def rebuild(self, glaciers: Iterable[str]) -> None:
        """Re-derive everything from disk (local meta.json files) and the bucket (remote meta.json files)."""
        fresh: dict = {}
        if config.PACKAGES_DIR.exists():
            for gdir in config.PACKAGES_DIR.iterdir():
                if not gdir.is_dir() or gdir.name.startswith("."):
                    continue
                for ydir in gdir.iterdir():
                    meta_path = ydir / "meta.json"
                    if ydir.name.isdigit() and meta_path.exists():
                        meta = json.loads(meta_path.read_text())
                        fresh.setdefault(gdir.name, {})[ydir.name] = summarize(meta, (ydir / storage.SYNCED).exists())
        try:
            for g in glaciers:
                for y in storage.remote_years(g):
                    if str(y) not in fresh.get(g, {}):
                        fresh.setdefault(g, {})[str(y)] = summarize(storage.read_remote_meta(g, y), True)
        except Exception:
            log.warning("bucket unreachable during rebuild; catalog is local-only for now", exc_info=True)
        with self._lock:
            self._data = fresh
            self._save_locked()


catalog = Catalog(config.CATALOG_FILE)
