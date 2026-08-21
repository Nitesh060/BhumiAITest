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


class TestTrainedModelOnlyFallback:
    """The actual production bug reported: /diagnose returned a hard
    503 ("AI diagnosis is currently unavailable... GEMINI_API_KEY")
    the moment Gemini was unavailable — even though the trained model
    sitting right next to it could answer perfectly well on its own.
    _trained_model_only_result is what /diagnose now falls back to in
    that case, so it must produce a usable, self-contained response
    from the trained model alone.
    """

    def test_none_when_plant_disease_model_unavailable(self, monkeypatch):
        monkeypatch.setattr(app_module, "plant_disease_model", None)
        assert app_module._trained_model_only_result(_TINY_PNG_BYTES) is None

    def test_none_when_classifier_has_no_checkpoint_yet(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: None
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        assert app_module._trained_model_only_result(_TINY_PNG_BYTES) is None

    def test_none_on_inference_exception(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")

        def _raise(image):
            raise RuntimeError("simulated inference failure")

        fake_module.classify_image = _raise
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        assert app_module._trained_model_only_result(_TINY_PNG_BYTES) is None  # must not raise

    def test_disease_prediction_builds_usable_result(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {
            "label": "Tomato___Late_blight", "confidence": 92.3, "top3": [],
        }
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = app_module._trained_model_only_result(_TINY_PNG_BYTES)

        assert result["is_plant"] is True
        assert result["crop_guess"] == "Tomato"
        assert result["category"] == "disease"
        assert result["diagnosis"] == "Late blight"
        assert result["confidence"] == "High"
        assert result["trained_model_only"] is True
        assert "Gemini" in result["caveat"]
        assert result["trained_model_prediction"]["label"] == "Tomato___Late_blight"

    def test_healthy_prediction_reports_no_issue(self, monkeypatch):
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")
        fake_module.classify_image = lambda image: {
            "label": "Corn_(maize)___healthy", "confidence": 65.7, "top3": [],
        }
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = app_module._trained_model_only_result(_TINY_PNG_BYTES)

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
            result = app_module._trained_model_only_result(_TINY_PNG_BYTES)
            assert result["confidence"] == expected_bucket, f"{confidence_pct}% should bucket to {expected_bucket}"
