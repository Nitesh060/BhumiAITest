"""Regression tests for _attach_trained_model_prediction (app.py), the
glue between /diagnose's existing Gemini call and the new trained
MobileNetV2 classifier (plant_disease_model.py — see ROADMAP.md Phase 14).

This function must never turn a working /diagnose response into a
failure: a missing checkpoint, a corrupt image, or plant_disease_model
being unavailable entirely (e.g. torch not installed — guarded at
app.py's import site) should only ever mean the extra field is omitted.
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


class TestAttachTrainedModelPrediction:
    def test_noop_when_plant_disease_model_unavailable(self, monkeypatch):
        """Mirrors the try/except import guard's failure case (e.g.
        torch/torchvision not installed in some environment) — must not
        raise, and must not touch the result dict at all."""
        monkeypatch.setattr(app_module, "plant_disease_model", None)
        result = {"diagnosis": "Late blight", "confidence": "High"}

        app_module._attach_trained_model_prediction(result, _TINY_PNG_BYTES)

        assert "trained_model_prediction" not in result

    def test_adds_prediction_when_available(self, monkeypatch):
        # app_module.PILImage is None in any environment where the
        # app.py import guard's torch/torchvision branch failed (e.g.
        # this test venv) — patch in the real PIL.Image so this test
        # exercises the actual image-decoding path regardless.
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {
            "label": "Tomato___Late_blight",
            "confidence": 92.3,
            "top3": [{"label": "Tomato___Late_blight", "confidence": 92.3}],
        }
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = {"diagnosis": "Late blight", "confidence": "High"}
        app_module._attach_trained_model_prediction(result, _TINY_PNG_BYTES)

        assert result["trained_model_prediction"]["label"] == "Tomato___Late_blight"
        assert result["diagnosis"] == "Late blight"  # Gemini's own fields untouched

    def test_noop_when_classifier_has_no_checkpoint_yet(self, monkeypatch):
        """classify_image() itself returns None (ships-untrained state,
        per plant_disease_model.py) — same as unavailable: omit the key."""
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: None
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = {"diagnosis": "Late blight"}
        app_module._attach_trained_model_prediction(result, _TINY_PNG_BYTES)

        assert "trained_model_prediction" not in result

    def test_never_raises_on_classifier_exception(self, monkeypatch):
        """The actual production concern this fixes: an inference-time
        crash (OOM, corrupt tensor, whatever) must degrade to 'no extra
        field', never take down the whole /diagnose response Gemini
        already successfully produced."""
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")

        def _raise(image):
            raise RuntimeError("simulated inference failure")

        fake_module.classify_image = _raise
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = {"diagnosis": "Late blight"}
        app_module._attach_trained_model_prediction(result, _TINY_PNG_BYTES)  # must not raise

        assert "trained_model_prediction" not in result

    def test_never_raises_on_corrupt_image_bytes(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {"label": "x", "confidence": 1.0, "top3": []}
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = {"diagnosis": "Late blight"}
        app_module._attach_trained_model_prediction(result, b"not a real image")  # must not raise

        assert "trained_model_prediction" not in result
