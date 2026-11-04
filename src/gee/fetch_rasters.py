# GEE will export (optical + elev + slope)

"""
src/gee/fetch_rasters.py

Config-driven Earth Engine fetch of the three rasters GlaViTU needs, per glacier and year:

    optical.tif    6 TOA reflectance bands: blue, green, red, nir, swir1, swir2   (float32, 0-1)
    elevation.tif  metres                                                         (static, once per glacier)
    slope.tif      degrees                                                        (static, once per glacier)

All three are written on ONE fixed 10 m UTM grid per glacier, so every year lines up pixel for pixel.
Do NOT normalise anything here: compile_features.py normalises with the authors' dataset statistics.

NOT RUN against Earth Engine yet (written without access). Check collection ids / band names in the
GEE catalog, then test with ONE glacier and ONE year before looping.

Colab setup:
    !pip install -q earthengine-api rasterio pyyaml pyproj
    import ee; ee.Authenticate()          # once per session
    %env EE_PROJECT=your-cloud-project

Usage:
    python src/gee/fetch_rasters.py survey --years 2000-2025            # how many scenes per glacier-year
    python src/gee/fetch_rasters.py fetch --glacier gulkana --years 2016
    python src/gee/fetch_rasters.py fetch --glacier all --years 2013-2020
"""
import argparse
import csv
import io
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import ee
import numpy as np
import rasterio
import yaml
from pyproj import Transformer
from rasterio.crs import CRS
from rasterio.transform import from_origin
from dotenv import load_dotenv


load_dotenv()

RES = 10            # the benchmark resampled everything to 10 m
TILE_PX = 1024      # 1024 x 1024 x 6 bands x float32 = about 25 MB, under the ~48 MB request limit
OPT_NAMES = ["blue", "green", "red", "nir", "swir1", "swir2"]

SENSORS = {
    "LT05": dict(col="LANDSAT/LT05/C02/T1_TOA", bands=["B1", "B2", "B3", "B4", "B5", "B7"]),
    "LE07": dict(col="LANDSAT/LE07/C02/T1_TOA", bands=["B1", "B2", "B3", "B4", "B5", "B7"]),
    "LC08": dict(col="LANDSAT/LC08/C02/T1_TOA", bands=["B2", "B3", "B4", "B5", "B6", "B7"]),
    "LC09": dict(col="LANDSAT/LC09/C02/T1_TOA", bands=["B2", "B3", "B4", "B5", "B6", "B7"]),
}
# Check which DEM the benchmark used for Alaska and keep it the same for every year.
DEMS = {
    "aw3d30": ("JAXA/ALOS/AW3D30/V3_2", "DSM"),
    "cop30": ("COPERNICUS/DEM/GLO30", "DEM"),
}
DEFAULTS = dict(season=["07-20", "09-20"], max_cloud=30, n_best=5, dem="aw3d30", use_l9=False)


# setup helpers
def init_ee():
    project = os.environ.get("EE_PROJECT")
    if not project:
        raise RuntimeError("Set EE_PROJECT to your Google Cloud project id")
    ee.Initialize(
        project=project,
        opt_url="https://earthengine-highvolume.googleapis.com",
    )


def load_config(path):
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["defaults"] = {**DEFAULTS, **cfg.get("defaults", {})}
    return cfg


def parse_years(text):
    years = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-")
            years += list(range(int(a), int(b) + 1))
        else:
            years.append(int(part))
    return years


def sensors_for_year(year, use_l9=False):
    if year <= 2011:
        return ["LT05"]                 # no SLC stripes
    if year == 2012:
        return ["LE07"]                 # only Landsat 7 (striped) -> flagged in the manifest
    return ["LC08", "LC09"] if (use_l9 and year >= 2022) else ["LC08"]   # Landsat 9 is not in the benchmark


def utm_epsg(lon, lat):
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def make_grid(bbox, res=RES, align=30):
    """Fixed UTM grid for a glacier. Corners are snapped to 30 m so it lines up with Landsat pixels."""
    w, s, e, n = bbox
    epsg = utm_epsg((w + e) / 2, (s + n) / 2)
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    pts = [tr.transform(x, y) for x, y in [(w, s), (w, n), (e, s), (e, n)]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    xmin = math.floor(min(xs) / align) * align
    xmax = math.ceil(max(xs) / align) * align
    ymin = math.floor(min(ys) / align) * align
    ymax = math.ceil(max(ys) / align) * align
    return dict(epsg=epsg, xmin=xmin, ymax=ymax, res=res,
                width=int((xmax - xmin) / res), height=int((ymax - ymin) / res))


# Earth Engine side
def optical_collection(bbox, year, sensors, season, max_cloud):
    aoi = ee.Geometry.Rectangle(bbox)
    col = None
    for s in sensors:
        cfg = SENSORS[s]
        part = (ee.ImageCollection(cfg["col"])
                .filterBounds(aoi)
                .filterDate(f"{year}-{season[0]}", f"{year}-{season[1]}")
                .filter(ee.Filter.lt("CLOUD_COVER", max_cloud))
                .select(cfg["bands"], OPT_NAMES))
        col = part if col is None else col.merge(part)
    return col


def build_dem(dem_key):
    ds_id, band = DEMS[dem_key]
    col = ee.ImageCollection(ds_id).select(band)
    proj = col.first().projection()
    dem = col.mosaic().setDefaultProjection(proj).rename("elevation")   # keep the native grid for slope
    slope = ee.Terrain.slope(dem).rename("slope")
    return dem.resample("bilinear").addBands(slope.resample("bilinear"))


def _compute_pixels(request, retries=4):
    for attempt in range(retries):
        try:
            return ee.data.computePixels(request)
        except ee.EEException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)


def fetch_array(image, grid, bands):
    """Pull `bands` on the fixed grid in tiles. Returns (H, W, len(bands)) float32."""
    out = np.zeros((grid["height"], grid["width"], len(bands)), np.float32)
    image = image.select(bands)
    for r0 in range(0, grid["height"], TILE_PX):
        for c0 in range(0, grid["width"], TILE_PX):
            h = min(TILE_PX, grid["height"] - r0)
            w = min(TILE_PX, grid["width"] - c0)
            request = {
                "expression": image,
                "fileFormat": "NPY",
                "grid": {
                    "dimensions": {"width": w, "height": h},
                    "affineTransform": {
                        "scaleX": grid["res"], "shearX": 0,
                        "translateX": grid["xmin"] + c0 * grid["res"],
                        "shearY": 0, "scaleY": -grid["res"],
                        "translateY": grid["ymax"] - r0 * grid["res"],
                    },
                    "crsCode": f"EPSG:{grid['epsg']}",
                },
            }
            arr = np.load(io.BytesIO(_compute_pixels(request)))       # structured array, one field per band
            out[r0:r0 + h, c0:c0 + w, :] = np.stack([arr[b] for b in bands], axis=-1)
    return out


def write_tif(path, array, grid, names):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path, "w", driver="GTiff", height=array.shape[0], width=array.shape[1],
        count=array.shape[2], dtype="float32", crs=CRS.from_epsg(grid["epsg"]),
        transform=from_origin(grid["xmin"], grid["ymax"], grid["res"], grid["res"]),
        compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(np.moveaxis(array, -1, 0))
        for i, name in enumerate(names, 1):
            dst.set_band_description(i, name)


# ---------------------------------------------------------------- commands
def fetch_static(name, g, cfg, out_root, force=False):
    d = cfg["defaults"]
    folder = out_root / name / "static"
    if (folder / "elevation.tif").exists() and (folder / "slope.tif").exists() and not force:
        return folder
    grid = make_grid(g["buffered_bbox_wgs84"])
    print(f"[{name}] DEM + slope: {grid['width']}x{grid['height']} px, EPSG:{grid['epsg']}")
    arr = fetch_array(build_dem(d["dem"]), grid, ["elevation", "slope"])
    write_tif(folder / "elevation.tif", arr[..., :1], grid, ["elevation_m"])
    write_tif(folder / "slope.tif", arr[..., 1:], grid, ["slope_deg"])
    (folder / "grid.json").write_text(json.dumps({**grid, "dem": d["dem"]}, indent=2))
    return folder


def fetch_year(name, g, year, cfg, out_root, force=False):
    d = cfg["defaults"]
    folder = out_root / name / str(year)
    if (folder / "optical.tif").exists() and not force:
        print(f"[{name} {year}] already fetched, skipping")
        return "cached"

    sensors = sensors_for_year(year, d["use_l9"])
    col = optical_collection(g["buffered_bbox_wgs84"], year, sensors, d["season"], d["max_cloud"])
    col = col.sort("CLOUD_COVER").limit(d["n_best"])
    n = col.size().getInfo()
    manifest = dict(glacier=name, year=year, sensors=sensors, season=d["season"],
                    max_cloud=d["max_cloud"], n_scenes=n, dem=d["dem"],
                    created=datetime.now(timezone.utc).isoformat(), warnings=[])
    folder.mkdir(parents=True, exist_ok=True)
    if n == 0:
        manifest["status"] = "no_scenes"
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
        print(f"[{name} {year}] no scenes (sensors {sensors}, cloud < {d['max_cloud']}%)")
        return "no_scenes"

    manifest["scene_ids"] = col.aggregate_array("system:index").getInfo()
    manifest["cloud_cover"] = col.aggregate_array("CLOUD_COVER").getInfo()
    if "LE07" in sensors:
        manifest["warnings"].append("Landsat 7 SLC-off stripes; composite may have gaps")
    if n < 2:
        manifest["warnings"].append("single scene: no cloud/stripe filling from the median")

    grid = make_grid(g["buffered_bbox_wgs84"])
    image = col.median().unmask(0)                       # no-data -> 0, like the benchmark tiles
    arr = fetch_array(image, grid, OPT_NAMES)
    write_tif(folder / "optical.tif", arr, grid, OPT_NAMES)

    valid = arr.max(axis=-1) > 0
    manifest["valid_fraction"] = float(valid.mean())
    if valid.any():
        manifest["optical_p1_p99"] = [float(np.percentile(arr[valid], 1)), float(np.percentile(arr[valid], 99))]
    manifest["status"] = "ok"
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[{name} {year}] ok: {n} scene(s), valid {manifest['valid_fraction']:.0%}, "
          f"p1-p99 {manifest.get('optical_p1_p99')}")
    return "ok"


def survey(cfg, names, years, out_csv):
    """Count usable late-summer scenes per glacier and year (cheap, server-side counts)."""
    d = cfg["defaults"]
    rows = []
    for name in names:
        bbox = cfg["glaciers"][name]["buffered_bbox_wgs84"]
        for y in years:
            sensors = sensors_for_year(y, d["use_l9"])
            col = optical_collection(bbox, y, sensors, d["season"], d["max_cloud"])
            rows.append([name, y, "+".join(sensors), col.size().getInfo()])
            print(rows[-1])
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["glacier", "year", "sensors", "n_scenes"])
        w.writerows(rows)
    print(f"wrote {out_csv}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["survey", "fetch"])
    p.add_argument("--config", default="config/glaciers.yaml")
    p.add_argument("--glacier", default="all")
    p.add_argument("--years", default="2016")
    p.add_argument("--out", default="data/raw")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    init_ee()
    cfg = load_config(args.config)
    names = list(cfg["glaciers"]) if args.glacier == "all" else [args.glacier]
    years = parse_years(args.years)
    out_root = Path(args.out)

    if args.command == "survey":
        survey(cfg, names, years, Path("data/survey_imagery.csv"))
        return

    summary = []
    for name in names:
        g = cfg["glaciers"][name]
        fetch_static(name, g, cfg, out_root, args.force)
        for y in years:
            try:
                summary.append((name, y, fetch_year(name, g, y, cfg, out_root, args.force)))
            except Exception as e:                       # keep going; log the failure
                print(f"[{name} {y}] FAILED: {e}")
                summary.append((name, y, "failed"))
    print("\nsummary:")
    for row in summary:
        print(*row)


if __name__ == "__main__":
    main()