"""Drop tiny polygon fragments from the polygonised model output.

Same logic as before, but the folder name is now an argument so the worker can call it for any glacier-year.

    python src/clipping/clean_tiny_polygon.py --input work/x/inference/glacier.shp --output-dir work/x/clean

Writes glacier_clean.shp, glacier_clean.geojson and clean_stats.json (before/after counts and areas,
which end up in meta.json).
"""
import argparse
import json
from pathlib import Path

import geopandas as gpd


def clean(input_path, output_dir, min_area_m2: float = 5000) -> dict:
    pred = gpd.read_file(input_path)
    if len(pred) == 0:
        raise ValueError("the model predicted no glacier polygons at all (check imagery / inference)")
    if pred.crs is None:
        raise ValueError(f"{input_path} has no CRS")
    if pred.crs.is_geographic:                       # area in m2 needs a metric CRS; take the UTM zone from the data
        pred = pred.to_crs(pred.estimate_utm_crs())

    pred["area_m2"] = pred.geometry.area
    before_n, before_km2 = len(pred), float(pred["area_m2"].sum() / 1e6)
    print("before:", before_n, "polygons, total km²:", round(before_km2, 4))

    kept = pred[pred["area_m2"] >= min_area_m2].copy()
    after_n, after_km2 = len(kept), float(kept["area_m2"].sum() / 1e6)
    print("after:", after_n, "polygons, total km²:", round(after_km2, 4))
    if after_n == 0:
        raise ValueError(f"every polygon is smaller than {min_area_m2} m²; lower --min-area-m2 or check the inference")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    kept.to_file(out / "glacier_clean.shp")
    kept.to_file(out / "glacier_clean.geojson", driver="GeoJSON")

    stats = {
        "min_area_m2": min_area_m2,
        "tile_polygons_before": before_n,
        "tile_polygons_after": after_n,
        "tile_area_km2_before": round(before_km2, 4),
        "tile_area_km2_after": round(after_km2, 4),
    }
    (out / "clean_stats.json").write_text(json.dumps(stats, indent=2))
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="polygonised model output (.shp / .geojson)")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--min-area-m2", type=float, default=5000, help="0.005 km²; raise to 10000-50000 if still noisy")
    a = ap.parse_args()
    clean(a.input, a.output_dir, a.min_area_m2)
