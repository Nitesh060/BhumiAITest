"""Regression tests for _diagnose_with_trained_model (app.py) — /diagnose's
sole diagnosis path (see ROADMAP.md Phase 14 / plant_disease_model.py).

Originally /diagnose called Gemini first and only used the trained
model as a cross-check or a fallback when Gemini failed. Per explicit
request, Gemini is no longer used at all here — /diagnose now always
answers from the trained MobileNetV2 classifier alone. This must never
raise: a missing checkpoint, a corrupt image, or plant_disease_model
being unavailable entirely (e.g. torch not installed — guarded at
app.py's import site) should only ever mean /diagnose returns its 503,
never a 500.
"""
import os
import sys

from PIL import Image as PILImage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module


# A 1x1 PNG — enough to exercise PIL.Image.open() without needing a real photo.
_TINY_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000155a2415e0000000049454e44ae426082"
)


class TestDiagnoseWithTrainedModel:
    def test_none_when_plant_disease_model_unavailable(self, monkeypatch):
        """Mirrors the try/except import guard's failure case (e.g.
        torch/torchvision not installed in some environment) — must not
        raise; /diagnose turns this None into its 503."""
        monkeypatch.setattr(app_module, "plant_disease_model", None)
        assert app_module._diagnose_with_trained_model(_TINY_PNG_BYTES) is None

    def test_none_when_classifier_has_no_checkpoint_yet(self, monkeypatch):
        """classify_image() itself returns None (ships-untrained state,
        per plant_disease_model.py)."""
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: None
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        assert app_module._diagnose_with_trained_model(_TINY_PNG_BYTES) is None

    def test_none_on_inference_exception(self, monkeypatch):
        """An inference-time crash (OOM, corrupt tensor, whatever) must
        degrade to None/503, never a raw 500."""
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")

        def _raise(image):
            raise RuntimeError("simulated inference failure")

        fake_module.classify_image = _raise
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        assert app_module._diagnose_with_trained_model(_TINY_PNG_BYTES) is None  # must not raise

    def test_none_on_corrupt_image_bytes(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {"label": "x", "confidence": 1.0, "top3": []}
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        assert app_module._diagnose_with_trained_model(b"not a real image") is None  # must not raise

    def test_disease_prediction_builds_usable_result(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {
            "label": "Tomato___Late_blight", "confidence": 92.3, "top3": [],
        }
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = app_module._diagnose_with_trained_model(_TINY_PNG_BYTES)

        assert result["is_plant"] is True
        assert result["crop_guess"] == "Tomato"
        assert result["category"] == "disease"
        assert result["diagnosis"] == "Late blight"
        assert result["confidence"] == "High"
        assert "Gemini" not in result["caveat"]  # no longer part of this flow at all
        assert result["trained_model_prediction"]["label"] == "Tomato___Late_blight"

    def test_healthy_prediction_reports_no_issue(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {
            "label": "Corn_(maize)___healthy", "confidence": 65.7, "top3": [],
        }
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = app_module._diagnose_with_trained_model(_TINY_PNG_BYTES)

        assert result["category"] == "healthy"
        assert result["diagnosis"] == "No obvious issue detected"

    def test_confidence_bucketing(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")

        for confidence_pct, expected_bucket in [(95.0, "High"), (80.0, "High"), (79.9, "Medium"), (50.0, "Medium"), (49.9, "Low")]:
            fake_module.classify_image = lambda image, c=confidence_pct: {
                "label": "Apple___healthy", "confidence": c, "top3": [],
            }
            monkeypatch.setattr(app_module, "plant_disease_model", fake_module)
            result = app_module._diagnose_with_trained_model(_TINY_PNG_BYTES)
            assert result["confidence"] == expected_bucket, f"{confidence_pct}% should bucket to {expected_bucket}"
