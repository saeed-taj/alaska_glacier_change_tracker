
from pathlib import Path

import geopandas as gpd
import yaml


# Change this path if your downloaded shapefile is elsewhere.
RGI_SHP = (
    Path.home()
    / "Alaska_glacier"
    / "alaska_glacier_change_tracker"
    / "RGI2000-v7.0-G-01_alaska"
    / "RGI2000-v7.0-G-01_alaska.shp"
)


OUTPUT_DIR = Path("data/reference")
CONFIG_DIR = Path("config")

TARGETS = [
    "Gulkana Glacier",
    "Wolverine Glacier",
    "Mendenhall Glacier",
]

TERMINUS_TYPES = {
    0: "land_terminating",
    1: "marine_terminating",
    2: "lake_terminating",
    3: "shelf_terminating",
    9: "not_assigned",
}


def find_glacier(gdf, target):
    names = gdf["glac_name"].fillna("").astype(str)

    # Prefer an exact name match.
    matches = gdf[names.str.casefold() == target.casefold()]

    # If there is no exact match, try a partial match.
    if len(matches) == 0:
        matches = gdf[
            names.str.contains(target, case=False, regex=False)
        ]

    if len(matches) != 1:
        print(f"\n{target}: expected one match, found {len(matches)}")

        if len(matches) > 0:
            columns = ["rgi_id", "glac_name", "area_km2"]
            print(matches[columns].to_string(index=False))

        print("Resolve this name manually before continuing.")
        return None

    return matches.iloc[0]


def main():
    if not RGI_SHP.exists():
        raise FileNotFoundError(
            f"Shapefile not found: {RGI_SHP}\n"
            "Update RGI_SHP to the location of your downloaded file."
        )

    gdf = gpd.read_file(RGI_SHP)

    required = {
        "rgi_id",
        "glac_name",
        "area_km2",
        "term_type",
        "geometry",
    }

    missing = required - set(gdf.columns)

    if missing:
        raise ValueError(
            f"Missing expected RGI columns: {sorted(missing)}\n"
            f"Available columns: {list(gdf.columns)}"
        )

    if gdf.crs is None:
        raise ValueError("The RGI shapefile has no defined CRS.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    selected_indices = []
    glaciers = {}

    for target in TARGETS:
        row = find_glacier(gdf, target)

        if row is None:
            continue

        # Buffer the glacier by 5 km in Alaska's projected CRS.
        projected = gpd.GeoSeries(
            [row.geometry], crs=gdf.crs
        ).to_crs("EPSG:3338")

        buffered = projected.iloc[0].buffer(5000)

        buffered_wgs84 = gpd.GeoSeries(
            [buffered], crs="EPSG:3338"
        ).to_crs("EPSG:4326").iloc[0]

        west, south, east, north = buffered_wgs84.bounds

        term_code = int(row["term_type"])
        term_name = TERMINUS_TYPES.get(
            term_code, "unknown"
        )

        # Stable, simple keys for the YAML configuration.
        key = target.lower().replace(" glacier", "").replace(" ", "_")

        glaciers[key] = {
            "name": str(row["glac_name"]),
            "rgi_id": str(row["rgi_id"]),
            "glims_id": (
                str(row["glims_id"])
                if "glims_id" in gdf.columns
                and row.get("glims_id") is not None
                else None
            ),
            "rgi_area_km2": float(row["area_km2"]),
            "terminus_type_code": term_code,
            "terminus_type": term_name,
            "bbox_buffer_km": 5,
            "buffered_bbox_wgs84": [
                round(float(west), 6),
                round(float(south), 6),
                round(float(east), 6),
                round(float(north), 6),
            ],
            "reference_data": {
                "rgi7_outline": True,
                "historical_validation": "not_checked",
            },
            "available_imagery_dates": [],
        }

        selected_indices.append(row.name)

        print(f"\n{target}")
        print(f"  RGI ID: {row['rgi_id']}")
        print(f"  Area: {row['area_km2']} km²")
        print(f"  Terminus type: {term_code} ({term_name})")
        print(f"  Buffered bbox: {west}, {south}, {east}, {north}")

    if not glaciers:
        raise RuntimeError(
            "No glaciers matched. Inspect the printed candidates "
            "and update TARGETS if the official names differ."
        )

    selected = gdf.loc[selected_indices].copy()

    selected.to_file(
        OUTPUT_DIR / "rgi7_alaska_targets.gpkg",
        driver="GPKG",
    )

    config = {
        "project": "Alaska Glacier Change Tracker",
        "inventory": {
            "name": "Randolph Glacier Inventory",
            "version": "7.0",
            "region": "01 - Alaska",
            "target_year": "approximately 2000",
            "source": (
                "https://nsidc.org/data/nsidc-0770/versions/7"
            ),
        },
        "glaciers": glaciers,
    }

    config_path = CONFIG_DIR / "glaciers.yaml"

    with config_path.open("w", encoding="utf-8") as file:
        yaml.safe_dump(
            config,
            file,
            sort_keys=False,
            allow_unicode=True,
        )

    print(f"\nSaved outlines to: {OUTPUT_DIR / 'rgi7_alaska_targets.gpkg'}")
    print(f"Saved configuration to: {config_path}")


if __name__ == "__main__":
    main()
