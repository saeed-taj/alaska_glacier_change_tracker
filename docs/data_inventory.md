## technical path (refined, decision-gate version)
## Phase 0 – Foundations (1–2 weeks)

Create clean repo structure (config/, src/, notebooks/, docs/, data/ gitignored).

Earth Engine auth + Cloud project.

Pick storage (local + cloud bucket; avoid free-tier limits for large GeoTIFFs).

Clone GlaViTU repo, inspect license of weights, run their demo inference to confirm environment.
Phase 1 – Glacier definition

RGI 7.0 Alaska → extract Gulkana, Wolverine, Mendenhall (IDs, area, terminus type).

Write config/glaciers.yaml (ID, name, buffered bbox, available reference data).


## Phase 2 – Data inspection & decision preparation

Download only the small demo / independent Alaska test subset first.

Confirm your three glaciers are outside the exact training tiles (or mark them for exclusion from any fine-tune evaluation).

Compute or load band mean/std from training tiles only → save JSON.


## Phase 3 – Zero-shot baseline (do this first)

Load pretrained Optical+DEM GlaViTU.

Build GEE composites + DEM + slope for the three glaciers, multiple years (start with 5–8 well-spaced years including overlap with NSIDC/USGS).

Run tiled inference with overlap, stitch, clip to RGI+buffer.

prediction raster (rectangle)
        ∩
RGI outline (polygon, optionally grown by 200–500 m)
        =
final glacier mask (only Gulkana)

Produce area (nominal / low-confidence / high-confidence) + quality flags.

Decision gate:

Landsat vs Sentinel agreement in overlap years?
Area vs NSIDC up to 2020 and USGS where available?
Visual QA on clean-ice vs debris vs lake terminus?

If good → skip training, go to validation + viewer.

If systematically bad → proceed to light fine-tune.

Buffer in yaml = larger rectangle around the glacier so GEE download and model context are complete.
RGI outline = used after inference to cut the prediction to only that glacier.
Clip = spatial mask with the polygon (rasterio.mask / similar), not concatenate.
GEE output = GeoTIFF rasters → then pickle for the model (same as demo).
BBOX → download; RGI → clip after prediction.


## Phase 4 – Optional fine-tune (only if gate fails)

Fine-tune the pretrained model (not from-scratch U-Net) on a mixed subset: AWA tiles + a few tiles that already contain L8/S2.

Low LR, few epochs, freeze most of the network or unfreeze last 2–3 blocks only.

Recalibrate confidence on the validation set after fine-tuning.

Save versioned artifact (model + stats + config).

Optional parallel experiment: small PyTorch U-Net for learning and open-source comparison (not required for GSoC core).


## Phase 5 – Full inference pipeline

CLI: glacier run --id gulkana --years 2000-2025 with caching, resume, scene logging (dates, cloud %, sensor).

Export stitched GeoTIFFs + per-year stats table.
Phase 6 – Validation & uncertainty

Quantitative table vs USGS / NSIDC / GLIMS.

Honest write-up of failure modes (especially Mendenhall).

Uncertainty bands from calibrated softmax.


## Phase 7 – Viewer (static prototype)

Heightmap from DEM + satellite drape, year slider, outlines, area chart with uncertainty, optional Hugoniot elevation-change layer.

Pure static web page (precomputed GeoJSON / PNG / JSON) — no server needed.
Phase 8 – Release hygiene

Tests (band order, normalization, DEM alignment, smoke test) in GitHub Actions.

README + model card + data sources + known limits.

One-command reproducibility.
GSoC / MLOps framing advice

Proposal language: Emphasize “reproducible multi-temporal segmentation + measured area change + uncertainty + interactive viewer for three well-studied Alaskan glaciers.” Explicitly say Releases 2–3 are post-GSoC.
Mentor expectation: Show you already understand the literature, the pretrained model, the sensor issues, and the need to start small.
MLOps: Treat every year/glacier as a cached, versioned artifact. Log every scene used. Make the pipeline resumable. Prefer static outputs for the viewer so storage and deployment stay simple.
Your existing HKH work: Mention it as evidence of capability, but do not assume the exact pipeline transfers; re-evaluate datasets and preprocessing for Alaska.