"""
plant_disease_model.py
========================
Plant-disease image classifier — a real trained-model counterpart to
the Layer 4 "Photo-Based AI" diagnosis, which today calls a
general-purpose vision model (see gemini_service.py) rather than a
custom-trained pipeline. See ROADMAP.md Phase 11/14.

Uses a MobileNetV2 backbone (ImageNet-pretrained, fine-tuned) rather
than a from-scratch CNN — for leaf-photo classification with tens of
thousands of training images per class (e.g. the PlantVillage dataset),
transfer learning gets to a useful accuracy far faster than training a
small custom CNN from zero, and MobileNetV2 is light enough to run
inference on a CPU-only Render dyno.

Ships UNTRAINED, exactly like land_cover_model.py: classify_image()
simply returns None until a trained checkpoint (plant_disease_model.pt)
and its matching class list (plant_disease_classes.json) are placed
next to this file. See train_plant_disease.py to train them.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn
from torchvision import transforms
from torchvision.models import mobilenet_v2, MobileNet_V2_Weights

IMAGE_SIZE = 224  # MobileNetV2's native ImageNet input size

MODEL_PATH = os.path.join(os.path.dirname(__file__), "plant_disease_model.pt")
CLASSES_PATH = os.path.join(os.path.dirname(__file__), "plant_disease_classes.json")

# Standard ImageNet normalization — required because the backbone's
# pretrained weights were trained against inputs normalized this way.
_PREPROCESS = transforms.Compose([
    transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def build_model(num_classes: int, pretrained: bool = False) -> nn.Module:
    """MobileNetV2 with its classifier head replaced for num_classes.

    pretrained=False (the default, used at inference time in
    _load_model below) never touches the network — the classifier's own
    trained checkpoint (loaded separately) is what actually matters at
    that point. pretrained=True is only ever passed from
    train_plant_disease.py, which needs internet access anyway to
    download the training dataset, to fetch MobileNetV2's real
    ImageNet weights as the transfer-learning starting point.
    """
    weights = MobileNet_V2_Weights.IMAGENET1K_V1 if pretrained else None
    model = mobilenet_v2(weights=weights)
    in_features = model.classifier[1].in_features
    model.classifier[1] = nn.Linear(in_features, num_classes)
    return model


_model: Optional[nn.Module] = None
_classes: Optional[List[str]] = None
_load_attempted = False


def _load_model() -> Optional[nn.Module]:
    global _model, _classes, _load_attempted
    if _model is not None:
        return _model
    if _load_attempted:
        return None
    _load_attempted = True

    if not (os.path.exists(MODEL_PATH) and os.path.exists(CLASSES_PATH)):
        return None

    with open(CLASSES_PATH) as f:
        _classes = json.load(f)

    m = build_model(num_classes=len(_classes))
    m.load_state_dict(torch.load(MODEL_PATH, map_location="cpu"))
    m.eval()
    _model = m
    return _model


def classify_image(image) -> Optional[Dict]:
    """Runs inference on a PIL.Image (RGB leaf photo).

    Returns None if no trained checkpoint is available yet, otherwise:
        {
          "label": "Tomato___Late_blight",
          "confidence": 92.3,
          "top3": [{"label": "...", "confidence": 92.3}, ...]
        }
    """
    model = _load_model()
    if model is None:
        return None

    tensor = _PREPROCESS(image.convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        logits = model(tensor)
        probs = torch.softmax(logits, dim=1).squeeze(0).numpy()

    order = np.argsort(probs)[::-1]
    top3 = [
        {"label": _classes[i], "confidence": round(float(probs[i]) * 100, 1)}
        for i in order[:3]
    ]
    return {
        "label": _classes[int(order[0])],
        "confidence": top3[0]["confidence"],
        "top3": top3,
    }
