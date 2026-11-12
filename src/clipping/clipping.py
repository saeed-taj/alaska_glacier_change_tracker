import json, os
import geopandas as gpd
import rasterio
from rasterio.mask import mask
from pyproj import Transformer
from shapely.geometry import Point, mapping

RGI_FILE   = "data/reference/rgi7_alaska_targets.gpkg"                  # your RGI v7 glacier file
PRED       = "gulkana_output/glacier_clean.shp"        # polygonised model output
RASTERS    = {"confidence": "gulkana_output/confidence.tif",
              "elevation":  "data/raw/gulkana/static/elevation.tif"}   # where you put the Gulkana DEM
RGI_ID     = "RGI2000-v7.0-G-01-05299"            # e.g. the ID from config/glaciers.yaml; None = look near the USGS Gulkana point
NAME, YEAR = "gulkana", 2015
BUFFER_M   = 500             # allowance for advance/retreat vs. the RGI outline

pred = gpd.read_file(PRED)
rgi = gpd.read_file(RGI_FILE).to_crs(pred.crs)
print("RGI columns:", list(rgi.columns))

# 1) pick the glacier in RGI
if RGI_ID:
    g = rgi[rgi["rgi_id"] == RGI_ID]
else:
    x, y = Transformer.from_crs(4326, pred.crs, always_xy=True).transform(-145.4675, 63.24083)
    cand = rgi[rgi.distance(Point(x, y)) < 3000]
    
    print(cand[["rgi_id", "glac_name", "area_km2"]].to_string())          # check this by eye
    
    named = cand[cand["glac_name"].fillna("").str.contains("Gulkana", case=False)]
    g = named if len(named) else cand.assign(d=cand.distance(Point(x, y))).nsmallest(1, "d")
assert len(g), "no RGI polygon found"


rgi_id = g["rgi_id"].iloc[0]
zone = g.dissolve().geometry.iloc[0].buffer(BUFFER_M)

# 2) keep only the predicted ice inside the RGI zone (the model ran on the full scene first)
clipped = gpd.clip(pred, zone)
clipped["km2"] = clipped.geometry.area / 1e6

print(f"{rgi_id}: predicted {clipped.km2.sum():.1f} km² | RGI {g['area_km2'].sum():.1f} km² "
      f"(RGI date {g['src_date'].iloc[0] if 'src_date' in g else 'n/a'})")

# 3) per-glacier, per-year package
out = f"packages/{NAME}/{YEAR}"
os.makedirs(out, exist_ok=True)
clipped.assign(rgi_id=rgi_id, year=YEAR).to_crs(4326).to_file(f"{out}/outline.geojson", driver="GeoJSON")

for key, path in RASTERS.items():
    with rasterio.open(path) as src:
        arr, tr = mask(src, [mapping(zone)], crop=True, nodata=-9999)
        meta = src.meta.copy()
        meta.update(height=arr.shape[1], width=arr.shape[2], transform=tr, nodata=-9999,
                    compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(f"{out}/{key}.tif", "w", **meta) as dst:
        dst.write(arr)
json.dump({"rgi_id": rgi_id, "name": NAME, "year": YEAR, "crs": str(pred.crs),
           "area_km2_predicted": round(float(clipped.km2.sum()), 3),
           "area_km2_rgi": round(float(g["area_km2"].sum()), 3),
           "buffer_m": BUFFER_M, "model": "GlaViTU global weights", "input_pixel_size_m": 10},
          open(f"{out}/meta.json", "w"), indent=2)
print("wrote:", sorted(os.listdir(out)))