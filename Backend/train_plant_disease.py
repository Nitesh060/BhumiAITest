"""
train_plant_disease.py
========================
Trains plant_disease_model.py's MobileNetV2 classifier on the
PlantVillage dataset:
  https://www.kaggle.com/datasets/abdallahalidev/plantvillage-dataset

This needs a real GPU-hours + internet budget (downloading both the
~2GB dataset and ImageNet-pretrained MobileNetV2 weights), which this
repo's Render deployment deliberately doesn't carry (see requirements.txt's
note on why torch/Pillow/torchvision are kept out of the deployed app).
Run this on your own machine or in Google Colab (free GPU, everything
below already preinstalled there) instead.

1. Download + unzip the dataset (Colab: Kaggle -> Settings -> API ->
   Create New Token -> upload kaggle.json, then:
     !pip install kaggle -q
     !mkdir -p ~/.kaggle && cp kaggle.json ~/.kaggle/
     !kaggle datasets download -d abdallahalidev/plantvillage-dataset
     !unzip -q plantvillage-dataset.zip -d plantvillage

   The dataset ships three parallel copies of the same 38 classes
   (color / grayscale / segmented) — use the "color" one; it's the
   closest match to real farmer phone-camera photos.

2. Install training-only deps (not needed by the deployed web app):
     pip install torch torchvision pillow

3. Run:
     python train_plant_disease.py --data ./plantvillage/color --epochs 10

   Produces plant_disease_model.pt + plant_disease_classes.json next to
   this file — copy both alongside plant_disease_model.py in the deployed
   app (or wherever crop_intelligence_service.py's photo-diagnosis code
   ends up calling classify_image() — see ROADMAP.md Phase 11/14) to
   make it start returning real predictions instead of None.
"""

from __future__ import annotations

import argparse
import json

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, random_split
from torchvision import transforms
from torchvision.datasets import ImageFolder

from plant_disease_model import IMAGE_SIZE, MODEL_PATH, CLASSES_PATH, build_model

# Light augmentation on top of plant_disease_model's own preprocessing —
# leaf photos in the field come in at arbitrary rotation/crop/lighting,
# unlike PlantVillage's fairly uniform lab-condition originals, so
# augmenting here helps the model generalize past the training set's
# controlled conditions.
_TRAIN_TRANSFORM = transforms.Compose([
    transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
    transforms.RandomHorizontalFlip(),
    transforms.RandomRotation(15),
    transforms.ColorJitter(brightness=0.2, contrast=0.2),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])
_VAL_TRANSFORM = transforms.Compose([
    transforms.Resize((IMAGE_SIZE, IMAGE_SIZE)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="Path to the dataset's 'color' folder (one subfolder per class)")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--val-split", type=float, default=0.15)
    ap.add_argument("--freeze-backbone", action="store_true",
                     help="Only train the new classifier head (faster, lower accuracy ceiling). "
                          "Default fine-tunes the whole network.")
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    full_ds = ImageFolder(args.data, transform=_TRAIN_TRANSFORM)
    classes = full_ds.classes  # sorted subfolder names, e.g. "Tomato___Late_blight"
    print(f"Found {len(classes)} classes, {len(full_ds)} images total")

    n_val = max(1, int(len(full_ds) * args.val_split))
    n_train = len(full_ds) - n_val
    train_ds, val_ds = random_split(full_ds, [n_train, n_val])
    # random_split shares the parent dataset's transform; validation
    # should use the un-augmented one to measure real generalization.
    val_ds.dataset = ImageFolder(args.data, transform=_VAL_TRANSFORM)

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=2)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, num_workers=2)

    # pretrained=True is the actual transfer-learning step — without it
    # we'd be training MobileNetV2 from random init, which needs far
    # more than a handful of epochs to converge.
    model = build_model(num_classes=len(classes), pretrained=True)
    if args.freeze_backbone:
        for param in model.features.parameters():
            param.requires_grad = False
    model.to(device)

    opt = torch.optim.Adam(
        (p for p in model.parameters() if p.requires_grad), lr=args.lr
    )
    loss_fn = nn.CrossEntropyLoss()

    best_val_acc = 0.0

    for epoch in range(args.epochs):
        model.train()
        total_loss, correct = 0.0, 0
        for x, y in train_loader:
            x, y = x.to(device), y.to(device)
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
                x, y = x.to(device), y.to(device)
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
            with open(CLASSES_PATH, "w") as f:
                json.dump(classes, f, indent=2)

    print(f"Saved best model (val_acc={best_val_acc:.3f}) to {MODEL_PATH}")
    print(f"Saved {len(classes)} class labels to {CLASSES_PATH}")


if __name__ == "__main__":
    main()
