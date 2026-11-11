import rasterio
from rasterio.mask import mask
import geopandas as gpd

# 1. Load RGI outline for Gulkana
gdf = gpd.read_file("data/reference/rgi7_alaska_targets.gpkg")
gulkana = gdf[gdf["rgi_id"] == "RGI2000-v7.0-G-01-05299"]

# optional: small buffer in metres (project to UTM first for accuracy)
# gulkana = gulkana.to_crs(epsg=32606)  # example UTM
# gulkana["geometry"] = gulkana.buffer(300)  # 300 m
# gulkana = gulkana.to_crs(epsg=4326)

# 2. Clip the prediction GeoTIFF
with rasterio.open("outlines.tif") as src:
    clipped_image, clipped_transform = mask(src, gulkana.geometry, crop=True, filled=True, nodata=0)
    # clipped_image is a numpy array; only glacier pixels remain non-zero