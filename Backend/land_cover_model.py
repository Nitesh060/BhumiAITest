"""
land_cover_model.py
====================
Lightweight CNN that classifies a 64x64 Sentinel-2 image patch
(Red, Green, Blue, NIR bands) into a coarse land-cover / crop-health
category, as an extra AI signal alongside the existing NDVI/NDMI/
rainfall/temperature/groundwater FarmScore components.

Ships UNTRAINED. Nothing here calls the internet or needs a GPU to
import — classify_patch() simply returns None until a trained
checkpoint (land_cover_cnn.pt) is placed next to this file.
See train_land_cover.py to train it on your own labelled patches.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

CLASSES = [
    "Healthy Cropland",
    "Stressed / Fallow Land",
    "Water Body",
    "Built-up / Barren",
]

PATCH_SIZE = 64
IN_CHANNELS = 4  # R, G, B, NIR

MODEL_PATH = os.path.join(os.path.dirname(__file__), "land_cover_cnn.pt")


class LandCoverCNN(nn.Module):
    """Small 3-conv-block CNN — deliberately tiny so it trains fast on
    a modest number of labelled patches (hundreds, not millions)."""

    def __init__(self, in_channels: int = IN_CHANNELS, num_classes: int = len(CLASSES)):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, 16, 3, padding=1)
        self.conv2 = nn.Conv2d(16, 32, 3, padding=1)
        self.conv3 = nn.Conv2d(32, 64, 3, padding=1)
        self.pool = nn.MaxPool2d(2, 2)
        reduced = PATCH_SIZE // 8
        self.fc1 = nn.Linear(64 * reduced * reduced, 128)
        self.fc2 = nn.Linear(128, num_classes)
        self.dropout = nn.Dropout(0.3)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.pool(F.relu(self.conv1(x)))
        x = self.pool(F.relu(self.conv2(x)))
        x = self.pool(F.relu(self.conv3(x)))
        x = x.flatten(1)
        x = self.dropout(F.relu(self.fc1(x)))
        return self.fc2(x)


_model: Optional[LandCoverCNN] = None
_load_attempted = False


def _load_model() -> Optional[LandCoverCNN]:
    global _model, _load_attempted
    if _model is not None:
        return _model
    if _load_attempted:
        return None
    _load_attempted = True

    if not os.path.exists(MODEL_PATH):
        return None

    m = LandCoverCNN()
    m.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    m.eval()
    _model = m
    return _model


def classify_patch(patch: np.ndarray) -> Optional[Dict]:
    """Run inference on a (64, 64, 4) reflectance patch (values 0-1).

    Returns None if no trained checkpoint is available yet, otherwise:
        {
          "label": "Healthy Cropland",
          "confidence": 87.4,
          "probabilities": {"Healthy Cropland": 87.4, ...}
        }
    """
    model = _load_model()
    if model is None:
        return None

    if patch.shape != (PATCH_SIZE, PATCH_SIZE, IN_CHANNELS):
        raise ValueError(
            f"Expected patch shape ({PATCH_SIZE}, {PATCH_SIZE}, {IN_CHANNELS}), got {patch.shape}"
        )

    tensor = torch.from_numpy(patch).float().permute(2, 0, 1).unsqueeze(0)
    with torch.no_grad():
        logits = model(tensor)
        probs = F.softmax(logits, dim=1).squeeze(0).numpy()

    idx = int(np.argmax(probs))
    return {
        "label": CLASSES[idx],
        "confidence": round(float(probs[idx]) * 100, 1),
        "probabilities": {c: round(float(p) * 100, 1) for c, p in zip(CLASSES, probs)},
    }
