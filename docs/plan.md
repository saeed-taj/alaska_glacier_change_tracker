## Phase 0: Setup
Create a GitHub repo with these folders: config/, src/, notebooks/, docs/, and a gitignored data/.
Set up Earth Engine: run ee.Authenticate() and register a Cloud project.
Pick storage. DagsHub’s free bucket is 10 GB, so it can hold only small outputs. Use cloud storage or Drive for big files.
Phase 1: Define your glaciers
Download RGI 7 for Alaska, and find Gulkana, Wolverine, and Mendenhall by name. Note each one’s ID, area, and terminus type.
Write config/glaciers.yaml with the ID, name, bounding box (the RGI outline plus a 2–5 km buffer), and which reference data exists for each.


## Phase 2: Training data
Download the 10% demo subset first. Inspect the file format, channels, label encoding, and dates.
Check whether your three glaciers fall inside AWA tiles. If they do, don’t evaluate on those tiles or on 2005/2010, because that would be testing on training data.

Download the AWA subset. I estimate about 324 tiles (95+32+32 plus 99+33+33), but I haven’t checked the file sizes.
Write a data loader. The paper used random 384×384 crops from tiles resampled to 10 m. 

Compute band mean and std from the training tiles only, and save them to JSON.


## Phase 3: Model
Run the authors’ pretrained model first. This lets you build the whole pipeline before training anything. It’s TensorFlow, and the code is GPL, so check the licenses for the weights too.
Train your own PyTorch U-Net on the AWA subset, using 8 channels: 6 bands, elevation, and slope. For scale, regional models took one to two days and finetuning a few hours to a day on an RTX A6000. A small U-Net on a subset should need less. 

Evaluate IoU on the test tiles, then on the independent Alaska set.
Add confidence: use the softmax output, then calibrate it on the validation set.
Save the model, stats, and config together as one versioned artifact.


## Phase 4: Fetch each glacier’s images
For each glacier and year, query GEE for late-summer scenes inside the bounding box. The NSIDC product used July 20 to September 20. 
nsidc

Pick the sensor by year: Landsat 5, 7, 8, or 9, or Sentinel-2 from 2015.
Use the TOA collections, because the benchmark is TOA. Check the exact names in the GEE catalog.
Build a median composite of the low-cloud scenes. Mask clouds, but never mask snow or ice.
Fetch DEM and slope from one consistent source, and compute slope with ee.Terrain.slope.
Resample to the same 10 m grid, the same way as training.
Export in pieces, since each request has a size limit. Stitch them into a GeoTIFF and cache it by glacier, year, and sensor.
Log the scene dates, cloud percentage, and sensor for every year, and flag the bad years.


## Phase 5: Inference and numbers
Run the model over the glacier tiles, with overlap, and stitch the results.
Assign pixels to your glacier. A mask can join neighboring glaciers, so clip it with the RGI outline plus a buffer.
Measure the area three ways: normal, low (only confident pixels), and high (including unsure pixels).
Save one table row per glacier and year: area, low, high, sensor, and quality flags.


## Phase 6: Validation
USGS. Download the benchmark data and list which years have boundaries. I haven’t checked which years those are. Run your pipeline for those years, and report IoU and percent area difference.
NSIDC 1985–2020. Download the shapefiles for your glaciers and compare areas over the overlapping years. This product is also model-made, so it shows consistency, not truth.
GLIMS. Query dated outlines on GEE and compare them too. Each record has src_date, the date of the source imagery. 
huaweicloud
Write up the results honestly, including Mendenhall, where I expect the lake terminus to cause errors.


## Phase 7: 3D viewer
Turn the DEM into a heightmap and drape it with the satellite image for each year.
Draw each year’s outline on top, with a year slider.
Add the Hugonnet elevation-change layer. Check the asset path in the GEE community catalog.
Show the area chart with its uncertainty band.
For the prototype, build a static web page reading precomputed files (heightmap images, GeoJSON, JSON stats). It needs no server, which avoids the Space storage problems we hit earlier.


## Phase 8: Automation and release
Build a command-line tool, like glacier run --id gulkana --years 2000-2025, with caching and resume.
Add tests for band order, normalization, and DEM alignment, plus a tiny smoke test. Run them in GitHub Actions.
Write the README, a model card, data sources, and known limits: lake-terminating glaciers, small glaciers, and debris.
“Prototype done” means
3 glaciers, with about 10–25 years each
Area over time with uncertainty
A validation table against USGS and NSIDC
A 3D viewer with a year slider
One command that reproduces everything
Rough timeline to early January
To Oct 31: Phases 0–2


November: Phases 3–5 (use the pretrained model first)
To mid-December: Phases 6–7
To early January: Phase 8 and the idea page