import rasterio
for f in ["optical", "elevation", "slope"]:
    with rasterio.open(f"src/preprocessing/demo_tiff_files/{f}.tiff") as s:
        a = s.read(masked=True)
        print(f, s.count, s.dtypes[0], s.shape, s.res, s.crs, float(a.min()), float(a.max()), s.nodata)