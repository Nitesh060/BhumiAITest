"""
train_land_cover.py
====================
Trains LandCoverCNN on a folder of labelled satellite patches.

Expected layout (each .npy is a (64, 64, 4) float32 array of
[R, G, B, NIR] reflectance, scaled 0-1 — exactly what
earth_engine_service.fetch_satellite_patch() returns):

    data/
      healthy_cropland/*.npy
      stressed_fallow/*.npy
      water_body/*.npy
      built_up_barren/*.npy

You need to build this dataset yourself — e.g. by calling
fetch_satellite_patch() for known-good locations of each class
(your own field visits / EPC survey points are a great source of
ground truth) and saving the returned array with np.save(). A few
hundred patches per class is enough to get a useful first model.

Usage:
    python train_land_cover.py --data ./data --epochs 20
"""

from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from land_cover_model import CLASSES, MODEL_PATH, LandCoverCNN

FOLDER_TO_CLASS = {
    "healthy_cropland": 0,
    "stressed_fallow": 1,
    "water_body": 2,
    "built_up_barren": 3,
}


class PatchDataset(Dataset):
    def __init__(self, data_dir: str):
        self.samples = []
        for folder, label in FOLDER_TO_CLASS.items():
            for path in glob.glob(os.path.join(data_dir, folder, "*.npy")):
                self.samples.append((path, label))
        if not self.samples:
            raise RuntimeError(
                f"No .npy patches found under {data_dir}. "
                f"Expected subfolders: {list(FOLDER_TO_CLASS)}"
            )

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int):
        path, label = self.samples[idx]
        patch = np.load(path).astype("float32")
        tensor = torch.from_numpy(patch).permute(2, 0, 1)  # HWC -> CHW
        return tensor, label


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Path to labelled patch folder")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--val-split", type=float, default=0.15)
    args = ap.parse_args()

    ds = PatchDataset(args.data)
    n_val = max(1, int(len(ds) * args.val_split))
    n_train = len(ds) - n_val
    train_ds, val_ds = torch.utils.data.random_split(ds, [n_train, n_val])

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size)

    model = LandCoverCNN()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    loss_fn = nn.CrossEntropyLoss()

    best_val_acc = 0.0

    for epoch in range(args.epochs):
        model.train()
        total_loss, correct = 0.0, 0
        for x, y in train_loader:
            opt.zero_grad()
            out = model(x)
            loss = loss_fn(out, y)
            loss.backward()
            opt.step()
            total_loss += loss.item() * x.size(0)
            correct += (out.argmax(1) == y).sum().item()
        train_acc = correct / n_train

        model.eval()
        val_correct = 0
        with torch.no_grad():
            for x, y in val_loader:
                out = model(x)
                val_correct += (out.argmax(1) == y).sum().item()
        val_acc = val_correct / n_val

        print(
            f"epoch {epoch + 1}/{args.epochs}  "
            f"loss={total_loss / n_train:.4f}  "
            f"train_acc={train_acc:.3f}  val_acc={val_acc:.3f}"
        )

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), MODEL_PATH)

    print(f"Saved best model (val_acc={best_val_acc:.3f}) to {MODEL_PATH}")
    print(f"Classes: {CLASSES}")


if __name__ == "__main__":
    main()
