
from pathlib import Path
import geopandas as gpd

# Change this to the actual location of your extracted shapefile.
shp_path = Path.home() / "Alaska_glacier" / "alaska_glacier_change_tracker" / "RGI2000-v7.0-G-01_alaska" / "RGI2000-v7.0-G-01_alaska.shp"


gdf = gpd.read_file(shp_path)

print("Number of glacier records:", len(gdf))
print("Coordinate reference system:", gdf.crs)
print("\nAvailable columns:")
print(gdf.columns.tolist())

print("\nFirst five records:")
print(gdf.head())

print("\nGlaciers matching our targets:")
names = gdf["glac_name"].fillna("").astype(str)

targets = ["Gulkana", "Wolverine", "Mendenhall"]

for target in targets:
    matches = gdf[
        names.str.contains(target, case=False, regex=False)
    ]

    print(f"\n{target}: {len(matches)} match(es)")

    columns = [
        col for col in
        ["rgi_id", "glac_name", "area_km2", "term_type"]
        if col in gdf.columns
    ]

    if not matches.empty:
        print(matches[columns].to_string(index=False))
