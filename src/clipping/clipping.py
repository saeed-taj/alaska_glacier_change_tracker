"""Clip the cleaned model output to ONE RGI glacier (+ buffer) and write the per-year package.

    python src/clipping/clipping.py --glacier gulkana --year 2013 \
        --pred work/x/clean/glacier_clean.shp --confidence work/x/inference/confidence.tif \
        --elevation data/raw/gulkana/static/elevation.tif --rgi-file data/reference/rgi7_alaska_targets.gpkg \
        --config config/glaciers.yaml --out-dir work/x/package

Writes outline.geojson, confidence.tif, elevation.tif and meta.json (meta.json last).

Changes from the notebook version:
  * every hardcoded value (folder, glacier, year, RGI id, buffer) is now an argument;
  * the RGI id comes from --rgi-id or from `rgi_id:` of that glacier in glaciers.yaml
    (the hardcoded Gulkana "look near this point" fallback is gone: it only worked for Gulkana);
  * meta.json now carries the full RGI attribute row plus numbers computed from the package itself
    (perimeter, elevation statistics, hypsometry, confidence statistics).
"""
import argparse
import json
import os
import platform
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import yaml
from rasterio import features
from rasterio.mask import mask
from shapely.geometry import mapping

NODATA = -9999
LOW_CONF = 0.5          # confidence below this counts as "low confidence"
BAND_M = 100            # hypsometry band height
SCHEMA_VERSION = 1


def _py(v):
    """numpy / pandas scalars -> plain JSON types (NaN and NaT -> None)."""
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, np.generic):
        v = v.item()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def resolve_rgi_id(rgi_id, glacier, config_path):
    if rgi_id:
        return rgi_id
    if config_path:
        cfg = yaml.safe_load(Path(config_path).read_text())
        found = cfg.get("glaciers", {}).get(glacier, {}).get("rgi_id")
        if found:
            return found
    raise ValueError(f"No RGI id for '{glacier}': pass --rgi-id or add `rgi_id:` to it in glaciers.yaml")


def _check_crs(src, crs):
    a, b = src.crs.to_epsg(), crs.to_epsg()
    if a is not None and b is not None and a != b:
        raise ValueError(f"CRS mismatch: raster is EPSG:{a}, polygons are EPSG:{b}")


def crop_raster(src_path, zone, dst_path, crs):
    with rasterio.open(src_path) as src:
        _check_crs(src, crs)
        arr, tr = mask(src, [mapping(zone)], crop=True, nodata=NODATA)
        meta = src.meta.copy()
        meta.update(height=arr.shape[1], width=arr.shape[2], transform=tr, nodata=NODATA,
                    compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(dst_path, "w", **meta) as dst:
        dst.write(arr)


def _union(gs):
    """One geometry from a GeoSeries (union_all needs geopandas >= 1.0; older versions use unary_union)."""
    return gs.union_all() if hasattr(gs, "union_all") else gs.unary_union


def rgi_agreement(pred_geom, rgi_geom, buffer_m) -> dict:
    """How the model outline and the RGI outline overlap. Both geometries must be in a metric CRS.

    The three pieces never overlap each other, so they add up exactly:
        overlap + predicted_outside_rgi = predicted area      overlap + rgi_not_predicted = RGI polygon area
    pred_geom is already clipped to RGI + buffer, so predicted_outside_rgi can never exceed that buffer strip.
    """
    overlap = pred_geom.intersection(rgi_geom).area
    outside = pred_geom.difference(rgi_geom).area
    missed = rgi_geom.difference(pred_geom).area
    km2 = lambda a: round(float(a) / 1e6, 4)
    union = overlap + outside + missed
    both = 2 * overlap + outside + missed
    return {
        "overlap_km2": km2(overlap),
        "predicted_outside_rgi_km2": km2(outside),
        "rgi_not_predicted_km2": km2(missed),
        "rgi_polygon_km2": km2(overlap + missed),            # from the geometry, may differ slightly from the attribute
        "iou": round(float(overlap / union), 4) if union else None,           # overlap / combined area
        "dice": round(float(2 * overlap / both), 4) if both else None,        # 2 * overlap / (predicted + RGI)
        "clip_buffer_m": buffer_m,
    }


def compute_metrics(pkg) -> dict:
    """Numbers derived ONLY from the files in a package, so this also works on packages made earlier."""
    pkg = Path(pkg)
    outline = gpd.read_file(pkg / "outline.geojson")
    with rasterio.open(pkg / "elevation.tif") as e, rasterio.open(pkg / "confidence.tif") as c:
        if (e.height, e.width) != (c.height, c.width):
            raise ValueError("elevation.tif and confidence.tif have different shapes")
        elev, conf = e.read(1), c.read(1)
        crs, transform, e_nodata, c_nodata = e.crs, e.transform, e.nodata, c.nodata
    outline = outline.to_crs(crs)                      # back to metres for area / perimeter
    geoms = list(outline.geometry)
    if not geoms:
        raise ValueError("no predicted glacier inside the RGI zone (check the imagery for this year)")

    inside = features.geometry_mask(geoms, out_shape=elev.shape, transform=transform, invert=True)
    px_km2 = abs(transform.a * transform.e) / 1e6
    ok_e = inside & np.isfinite(elev) & (elev != e_nodata)
    ok_c = inside & np.isfinite(conf) & (conf != c_nodata)

    metrics = {
        "area_km2_predicted": round(float(outline.geometry.area.sum() / 1e6), 4),
        "area_km2_pixel_count": round(float(inside.sum() * px_km2), 4),     # cross-check of the polygon area
        "n_polygons": int(len(outline)),
        "perimeter_km": round(float(outline.geometry.length.sum() / 1e3), 3),
        "elevation_m": None, "hypsometry": None, "confidence": None,
    }
    if ok_e.any():
        v = elev[ok_e]
        metrics["elevation_m"] = {"min": round(float(v.min()), 1), "median": round(float(np.median(v)), 1),
                                  "mean": round(float(v.mean()), 1), "max": round(float(v.max()), 1)}
        lo, hi = int(np.floor(v.min() / BAND_M) * BAND_M), int(np.ceil(v.max() / BAND_M) * BAND_M)
        edges = np.arange(lo, max(hi, lo + BAND_M) + BAND_M, BAND_M)
        counts, _ = np.histogram(v, bins=edges)
        metrics["hypsometry"] = {"band_m": BAND_M, "bands": [
            {"lo_m": int(edges[i]), "hi_m": int(edges[i + 1]), "area_km2": round(float(n * px_km2), 4)}
            for i, n in enumerate(counts) if n > 0]}
    if ok_c.any():
        v = conf[ok_c]
        metrics["confidence"] = {"mean": round(float(v.mean()), 4), "low_conf_threshold": LOW_CONF,
                                 "low_conf_fraction": round(float((v < LOW_CONF).mean()), 4)}
    return metrics


def clip(pred_path, rgi_file, rgi_id, confidence, elevation, out_dir, glacier, year, buffer_m=100) -> dict:
    pred = gpd.read_file(pred_path)
    rgi = gpd.read_file(rgi_file).to_crs(pred.crs)
    g = rgi[rgi["rgi_id"] == rgi_id]
    if not len(g):
        raise ValueError(f"{rgi_id} not found in {rgi_file}")
    zone = g.dissolve().geometry.iloc[0].buffer(buffer_m)         # RGI outline + allowance for advance/retreat

    # keep only the predicted ice inside the RGI zone (the model ran on the whole scene)
    clipped = gpd.clip(pred, zone)
    clipped = clipped[clipped.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if clipped.empty:
        raise ValueError(f"no predicted glacier inside the RGI zone of {rgi_id} for {year}")
    clipped["km2"] = clipped.geometry.area / 1e6

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clipped.assign(rgi_id=rgi_id, year=year).to_crs(4326).to_file(out / "outline.geojson", driver="GeoJSON")
    crop_raster(confidence, zone, out / "confidence.tif", pred.crs)
    crop_raster(elevation, zone, out / "elevation.tif", pred.crs)

    metrics = compute_metrics(out)
    rgi_area = float(g["area_km2"].sum())
    diff = metrics["area_km2_predicted"] - rgi_area
    results = {
        "area_km2_predicted": metrics.pop("area_km2_predicted"),
        "area_km2_rgi": round(rgi_area, 4),
        "area_diff_km2": round(diff, 4),
        "area_diff_pct": round(diff / rgi_area * 100, 2) if rgi_area else None,
        "rgi_agreement": rgi_agreement(_union(clipped.geometry), _union(g.geometry), buffer_m),
        **metrics,
    }
    rgi_row = g.drop(columns="geometry").iloc[0]
    with rasterio.open(out / "elevation.tif") as e:
        grid = {"crs": e.crs.to_string(), "pixel_size_m": abs(e.transform.a), "shape": [e.height, e.width],
                "bounds": [round(b, 1) for b in e.bounds]}
    meta = {
        "schema_version": SCHEMA_VERSION,
        "glacier": {"name": glacier, "rgi_id": rgi_id},
        "year": year,
        "results": results,
        "rgi": {**{k: _py(v) for k, v in rgi_row.items()}, "polygons_matched": int(len(g))},
        "postprocessing": {"clip_buffer_m": buffer_m},
        "grid": grid,
        "software": {"python": platform.python_version(), "geopandas": gpd.__version__,
                     "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__},
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2))          # meta.json LAST

    print(f"{rgi_id}: predicted {results['area_km2_predicted']:.1f} km² | RGI {rgi_area:.1f} km² "
          f"(RGI date {meta['rgi'].get('src_date', 'n/a')})")
    print("wrote:", sorted(os.listdir(out)))
    return meta


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glacier", required=True)
    ap.add_argument("--year", required=True, type=int)
    ap.add_argument("--pred", required=True, help="glacier_clean.shp from clean_tiny_polygon.py")
    ap.add_argument("--confidence", required=True)
    ap.add_argument("--elevation", required=True)
    ap.add_argument("--rgi-file", required=True)
    ap.add_argument("--rgi-id", default=None)
    ap.add_argument("--config", default=None, help="glaciers.yaml (source of rgi_id when --rgi-id is not given)")
    ap.add_argument("--buffer-m", type=float, default=100)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    clip(a.pred, a.rgi_file, resolve_rgi_id(a.rgi_id, a.glacier, a.config), a.confidence, a.elevation,
         a.out_dir, a.glacier, a.year, a.buffer_m)
    