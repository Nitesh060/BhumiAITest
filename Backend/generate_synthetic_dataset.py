"""
generate_synthetic_dataset.py
==============================
Generates a FAKE but plausibly-shaped dataset of (64, 64, 4) [R, G, B,
NIR] reflectance patches, one folder per class, so you can smoke-test
the whole CNN pipeline (train_land_cover.py -> land_cover_cnn.pt ->
land_cover_model.classify_patch()) end-to-end before you have any real
labelled satellite patches.

IMPORTANT: this data is NOT real satellite imagery. A model trained
only on this will learn the synthetic rules below, not real land
cover — it exists purely to prove the pipeline runs, catch shape/dtype
bugs, and let you sanity-check the training loop and accuracy curve.
Swap in real patches from earth_engine_service.fetch_satellite_patch()
(saved with the same folder/np.save layout) before trusting any
output for actual credit decisions.

Each class gets a distinct, deliberately-approximate reflectance
signature with per-pixel texture noise and a few synthetic "blob"
features so patches inside a class aren't identical:

    healthy_cropland  -> low red, high NIR   (high vegetation index)
    stressed_fallow    -> moderate red, low-moderate NIR, soil-brown tint
    water_body          -> low across all bands, NIR near zero
    built_up_barren    -> high, flat reflectance across all bands

Usage:
    python generate_synthetic_dataset.py --out ./data --per-class 200
    python generate_synthetic_dataset.py --out ./data --per-class 200 --preview
"""

from __future__ import annotations

import argparse
import os

import numpy as np

PATCH_SIZE = 64

# Per-class (mean, std) reflectance for each band, in [R, G, B, NIR] order.
# Values are illustrative, not calibrated to real Sentinel-2 statistics.
CLASS_SIGNATURES = {
    "healthy_cropland": {
        "mean": np.array([0.08, 0.12, 0.06, 0.50]),
        "std": np.array([0.02, 0.02, 0.015, 0.06]),
    },
    "stressed_fallow": {
        "mean": np.array([0.22, 0.20, 0.14, 0.24]),
        "std": np.array([0.03, 0.03, 0.02, 0.04]),
    },
    "water_body": {
        "mean": np.array([0.04, 0.06, 0.09, 0.02]),
        "std": np.array([0.01, 0.01, 0.015, 0.01]),
    },
    "built_up_barren": {
        "mean": np.array([0.32, 0.31, 0.29, 0.34]),
        "std": np.array([0.03, 0.03, 0.03, 0.03]),
    },
}


def _smooth_noise(rng: np.random.Generator, size: int = PATCH_SIZE) -> np.ndarray:
    """Cheap low-frequency texture: upsample a small random grid."""
    small = rng.normal(0, 1, size=(8, 8))
    x = np.linspace(0, 7, size)
    y = np.linspace(0, 7, size)
    xi = np.clip(x.astype(int), 0, 7)
    yi = np.clip(y.astype(int), 0, 7)
    coarse = small[np.ix_(yi, xi)]
    # bilinear-ish smoothing pass
    coarse = (
        coarse
        + np.roll(coarse, 1, axis=0)
        + np.roll(coarse, -1, axis=0)
        + np.roll(coarse, 1, axis=1)
        + np.roll(coarse, -1, axis=1)
    ) / 5.0
    return coarse


def _make_patch(rng: np.random.Generator, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    patch = np.zeros((PATCH_SIZE, PATCH_SIZE, 4), dtype="float32")

    # Per-patch random offset so patches within a class still vary.
    patch_offset = rng.normal(0, std * 0.4, size=4)

    for b in range(4):
        texture = _smooth_noise(rng) * std[b]
        pixel_noise = rng.normal(0, std[b] * 0.3, size=(PATCH_SIZE, PATCH_SIZE))
        band = mean[b] + patch_offset[b] + texture + pixel_noise
        patch[:, :, b] = band

    return np.clip(patch, 0.0, 1.0).astype("float32")


def generate(out_dir: str, per_class: int, seed: int, preview: bool) -> None:
    rng = np.random.default_rng(seed)

    for folder, sig in CLASS_SIGNATURES.items():
        class_dir = os.path.join(out_dir, folder)
        os.makedirs(class_dir, exist_ok=True)

        for i in range(per_class):
            patch = _make_patch(rng, sig["mean"], sig["std"])
            np.save(os.path.join(class_dir, f"{folder}_{i:04d}.npy"), patch)

        print(f"  {folder:<18s} -> {per_class} patches in {class_dir}")

    print(f"\nDone. {per_class * len(CLASS_SIGNATURES)} synthetic patches written to {out_dir}")
    print("Next: python train_land_cover.py --data", out_dir)

    if preview:
        _save_preview(out_dir, rng)


def _save_preview(out_dir: str, rng: np.random.Generator) -> None:
    """Save a quick RGB grid (one row per class) as preview.png so you
    can eyeball that the classes actually look visually distinct."""
    from PIL import Image

    cols = 6
    rows = len(CLASS_SIGNATURES)
    grid = np.zeros((rows * PATCH_SIZE, cols * PATCH_SIZE, 3), dtype="uint8")

    for r, (folder, sig) in enumerate(CLASS_SIGNATURES.items()):
        for c in range(cols):
            patch = _make_patch(rng, sig["mean"], sig["std"])
            rgb = np.clip(patch[:, :, :3] * 3.5, 0, 1)  # brighten for visibility
            grid[r * PATCH_SIZE:(r + 1) * PATCH_SIZE, c * PATCH_SIZE:(c + 1) * PATCH_SIZE] = (
                rgb * 255
            ).astype("uint8")

    preview_path = os.path.join(out_dir, "preview.png")
    Image.fromarray(grid).save(preview_path)
    print(f"Preview grid (one row per class) saved to {preview_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="./data", help="Output folder (default ./data)")
    ap.add_argument("--per-class", type=int, default=200, help="Patches per class")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--preview", action="store_true", help="Also save a preview.png grid")
    args = ap.parse_args()

    print(f"Generating synthetic dataset in {args.out} ({args.per_class} patches/class)...\n")
    generate(args.out, args.per_class, args.seed, args.preview)


if __name__ == "__main__":
    main()
