"""
Clean inference for GlaViTU (optical + dem).
Works with TF 2.20 / 2.21 and the released .h5 weights.
"""
import os, pickle, gc
import numpy as np
import tensorflow as tf
from tqdm import tqdm
import rasterio
import scipy.ndimage

# ---- minimal config so imports succeed ----
import configs.data as data_cfg
data_cfg.batch_size   = 4
data_cfg.patch_size   = 384
data_cfg.n_outputs    = 2
data_cfg.features     = ["optical", "dem"]
data_cfg.input_shapes = {
    "optical": (384, 384, 6),
    "dem":     (384, 384, 2),
}
data_cfg.train_sampler_args = {
    "dataset": None, "patch_size": 384,
    "features": ["optical", "dem"], "labels": "outlines"
}
data_cfg.val_sampler_args = data_cfg.train_sampler_args.copy()
data_cfg.train_plugins = []
data_cfg.val_plugins   = []

import models.mapping
from utils.utils import build_model

# ---- build model ----
model_builder = models.mapping.GlaViTU
model_args = {
    "last_activation": "softmax",
    "dropout": 0.0,
    "inference_dropout": False,
    "name": "GlaViTU",
    "use_deepsupervision": True,
}

# Change this line to switch between the three weight files
WEIGHTS = "weights/glavitu_global_weights.h5"
# WEIGHTS = "weights/glavitu_regional_AWA_weights.h5"
# WEIGHTS = "weights/glavitu_finetuning_AWA_weights.h5"

print("Building model …")
model = build_model(
    model_builder, model_args,
    data_config=data_cfg,
    weights_path=None,
    mode="testing"
)

print("Loading weights …")
try:
    model.load_weights(WEIGHTS)
    print("Strict load OK")
except Exception as e:
    print("Strict load failed → trying by_name + skip_mismatch")
    print("Error was:", e)
    model.load_weights(WEIGHTS, by_name=True, skip_mismatch=True)
    print("Loaded with by_name=True, skip_mismatch=True")

# ---- tiled inference ----
def gaussian_kernel(size, mu=0, sigma=1):
    x, y = np.meshgrid(np.linspace(-1, 1, size), np.linspace(-1, 1, size))
    d = np.sqrt(x**2 + y**2)
    return np.exp(-(d - mu)**2 / (2 * sigma**2)) / (np.sqrt(2 * np.pi) * sigma)

def apply_model(model, features, patch_size=384, batch_size=4):
    h, w, _ = features["optical"].shape
    weighted = np.zeros((h, w, 2), np.float32)
    counts   = np.zeros((h, w, 1), np.float32)
    kw = gaussian_kernel(patch_size)[..., None].astype(np.float32)

    patches = {k: [] for k in features if k not in ("lat", "lon")}
    rows_cols, n = [], 0

    row = 0
    while row + patch_size <= h:
        col = 0
        while col + patch_size <= w:
            for k, arr in features.items():
                if k in ("lat", "lon"):
                    continue
                patches[k].append(arr[row:row+patch_size, col:col+patch_size])
            rows_cols.append((row, col))
            n += 1
            if n >= batch_size:
                batch = {k: np.asarray(v) for k, v in patches.items()}
                pred = model.predict(batch, verbose=0)
                for (r, c), p in zip(rows_cols, pred):
                    weighted[r:r+patch_size, c:c+patch_size] += kw * p
                    counts  [r:r+patch_size, c:c+patch_size] += kw
                patches = {k: [] for k in patches}
                rows_cols, n = [], 0
            col += patch_size // 2
        row += patch_size // 2

    if n:
        batch = {k: np.asarray(v) for k, v in patches.items()}
        pred = model.predict(batch, verbose=0)
        for (r, c), p in zip(rows_cols, pred):
            weighted[r:r+patch_size, c:c+patch_size] += kw * p
            counts  [r:r+patch_size, c:c+patch_size] += kw

    return weighted / np.maximum(counts, 1e-8)

# ---- main ----
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("input")
    p.add_argument("-o", "--output", default="demo_output")
    p.add_argument("--smoothing", type=int, default=3)
    args = p.parse_args()

    os.makedirs(args.output, exist_ok=True)

    with open(args.input, "rb") as f:
        features, attrs = pickle.load(f)

    h, w = attrs["height"], attrs["width"]
    pad_h, pad_w = attrs["pad_height"], attrs["pad_width"]
    meta = attrs["meta"]

    print(f"optical {features['optical'].shape}, dem {features['dem'].shape}")
    print("Running inference …")

    probs = apply_model(model, features)
    probs = probs[pad_h:pad_h+h, pad_w:pad_w+w]
    pred  = np.argmax(probs, -1).astype(np.uint8)

    if args.smoothing:
        pred = scipy.ndimage.median_filter(pred, size=args.smoothing)

    conf = np.max(probs, -1).astype(np.float32)

    meta.update(count=1, dtype="uint8")
    with rasterio.open(os.path.join(args.output, "outlines.tif"), "w", **meta) as dst:
        dst.write(pred, 1)

    meta.update(dtype="float32")
    with rasterio.open(os.path.join(args.output, "confidence.tif"), "w", **meta) as dst:
        dst.write(conf, 1)

    print("Done!")
    print(f"Saved: {args.output}/outlines.tif")
    print(f"Saved: {args.output}/confidence.tif")
