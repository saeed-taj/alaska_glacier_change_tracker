yaml bbox  ──────────────────────────────────────────────►  GEE export
                                                              │
                                                    optical / elev / slope .tif
                                                              │
                                                    compile_features → pickle
                                                              │
                                                    GlaViTU inference
                                                              │
                                                    outlines.tif (whole rectangle)
                                                              │
RGI outline (+ small buffer)  ─────────────────────────────►  CLIP
                                                              │
                                                    gulkana-only mask
                                                              │
                                                    area + confidence + quality flags





User selects glacier + date(s)
        │
        ▼
Frontend requests the corresponding .tar.gz from DagsHub
        │
        ▼
Unpack (or stream) the package
        │
        ▼
Load:
  - elevation → 3D terrain
  - glacier.geojson → glacier outline / surface
  - optical_rgb → texture
  - (optional) confidence → transparency or quality indicator
        │
        ▼
Render 3D view (Cesium / Three.js / Mapbox …)

### RGI (Randolph Glacier Inventory) = a global catalog of glacier outlines (polygons).

Version 7.0 is what you’re using

For each glacier it has: ID, name, area, geometry (the outline), etc.
For Alaska it is largely based on imagery around ~2000 (and related sources)

So RGI7 is a reference inventory, not a live sensor product.
It is the best widely used “official” outline for many glaciers, so people treat it as ground truth for validation — with the important caveat that it is old (≈ year 2000), not “truth for 2015/2020”.

What your model produces
For a given glacier + year (e.g. Gulkana 2015):





















OutputMeaningoutlines.tif / glacier.geojsonModel’s predicted glacier shape for that yearconfidence.tifHow sure the model is, per pixelArea from the polygonModel’s estimated glacier area (km²) in 2015

Two different comparisons (don’t mix them up)
1) Model vs RGI7 — validation / quality check
textModel outline (2015)  vs  RGI7 outline (~2000)
You measure things like:





















MetricMeaningOverlap (IoU)How much the two shapes agree (0–1)Area differenceModel area − RGI area (km² or %)Extra / missing partsWhere model adds ice RGI doesn’t have, or misses ice RGI has
Purpose:

“Does the model look reasonable compared to the standard inventory?”
Important:

A difference is not only model error.

Glaciers change over 15–25 years, so part of the gap is real retreat/advance.

Still useful as a sanity check and for observability (“IoU vs RGI7 = 0.88”).

2) Model year A vs model year B — change over time
textModel outline 2015  vs  Model outline 2018
You measure:


        


Metric MeaningArea changeArea_2018 − Area_2015 (km² and %)Spatial changeWhere the front retreated/advanced
Purpose:

This is the science product: how the glacier changed between years.
Here both sides come from your pipeline, so the comparison is consistent (same method, same grid style).

How it works in practice
Once (setup)

Load RGI7 polygon for Gulkana from your .gpkg / .shp

After each inference (e.g. 2015)

Get model polygon + area
Compare to RGI7 → write into metrics.json:
area_model_km2
area_rgi_km2
iou_vs_rgi
area_diff_km2, area_diff_pct

If another year exists (e.g. 2018):
Compare 2015 vs 2018 → area_change_km2, area_change_pct


Frontend can show

Outline for selected year
“vs RGI7: IoU 0.87, −1.2 km²”
“2015 → 2018: −0.4 km² (−2.3%)” when two years exist
Confidence overlay


Short answers to your questions

Question                                        Answer 
Is RGI the inventory / ground truth?            Yes — reference inventory. Best available global GT,
                                                but   dated (~2000).
Do we cross-check model vs RGI?                 Yes — for quality / observability, not as “error 
                                                = difference”.
Is year-to-year change from RGI?                No. Year-to-year change = model 2015 vs model 2018.
Are you wrong?                                  Almost right: RGI = reference check; change over time =                                               model vs model across years.