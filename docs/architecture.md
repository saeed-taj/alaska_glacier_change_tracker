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