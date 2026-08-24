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
import io
import os
import sys

from PIL import Image as PILImage
from PIL import ImageFile as PILImageFile
from PIL import ImageOps as PILImageOps

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module


# A 1x1 PNG — enough to exercise PIL.Image.open() without needing a real photo.
_TINY_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000155a2415e0000000049454e44ae426082"
)


def _jpeg_with_exif_orientation(size, orientation: int) -> bytes:
    """A real (width, height) JPEG carrying an EXIF Orientation tag —
    the same shape a phone camera photo has: pixels stored un-rotated,
    with a tag saying how a viewer should rotate them."""
    img = PILImage.new("RGB", size, color=(120, 200, 90))
    exif = img.getexif()
    exif[274] = orientation  # 274 = the EXIF Orientation tag id
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


def _truncated_jpeg_bytes(size=(200, 200)) -> bytes:
    """A real JPEG with its tail cut off — simulates a phone upload
    interrupted by a flaky connection partway through the (much larger)
    pixel-scan data, after the small header/metadata section at the
    start of the file already arrived intact. LOAD_TRUNCATED_IMAGES
    only helps with truncation here, in the pixel data — a cut through
    the header itself is unrecoverable no matter what, same as a
    genuinely corrupt file."""
    img = PILImage.new("RGB", size, color=(10, 200, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    full = buf.getvalue()
    return full[: int(len(full) * 0.9)]


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


class TestPhoneCameraImageRobustness:
    """Real phone-camera uploads have two quirks lab-sourced test images
    don't: an EXIF orientation tag instead of physically rotated pixels,
    and (over a flaky mobile connection) occasional truncation. Neither
    should silently hurt accuracy or turn into a 503."""

    def test_exif_orientation_is_corrected_before_classification(self, monkeypatch):
        """A sideways phone photo (100x50 pixels, tagged 'rotate to
        50x100') must reach the classifier already rotated — otherwise
        the model sees the leaf on its side, which plant_disease_model's
        MobileNetV2 backbone was never trained to expect."""
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        monkeypatch.setattr(app_module, "PILImageOps", PILImageOps)
        seen_sizes = []
        fake_module = type(sys)("fake_plant_disease_model")

        def _capture(image):
            seen_sizes.append(image.size)
            return {"label": "Tomato___healthy", "confidence": 90.0, "top3": []}

        fake_module.classify_image = _capture
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        image_bytes = _jpeg_with_exif_orientation((100, 50), orientation=6)
        result = app_module._diagnose_with_trained_model(image_bytes)

        assert result is not None
        assert seen_sizes == [(50, 100)]  # rotated, not the raw 100x50

    def test_truncated_upload_still_produces_a_diagnosis(self, monkeypatch):
        """A phone upload cut short by a flaky connection used to raise
        inside PIL's pixel decoding, turning into a 503 even though most
        of the image data actually arrived. PIL.Image.open() only parses
        the header lazily — the truncation only actually surfaces once
        something forces pixel access, exactly like the real
        classify_image()'s own .convert("RGB") call does.

        app.py sets ImageFile.LOAD_TRUNCATED_IMAGES = True as a side
        effect of its top-level `import plant_disease_model` try block —
        which doesn't run in this sandbox (no torch installed here), so
        the flag is set directly here instead, the same way the other
        tests in this file monkeypatch PILImage/plant_disease_model
        rather than depending on that same import having actually
        succeeded for real.
        """
        monkeypatch.setattr(PILImageFile, "LOAD_TRUNCATED_IMAGES", True)
        monkeypatch.setattr(app_module, "PILImage", PILImage)
        fake_module = type(sys)("fake_plant_disease_model")

        def _load_then_classify(image):
            image.load()  # forces the truncated pixel-scan data to decode
            return {"label": "Potato___Early_blight", "confidence": 70.0, "top3": []}

        fake_module.classify_image = _load_then_classify
        monkeypatch.setattr(app_module, "plant_disease_model", fake_module)

        result = app_module._diagnose_with_trained_model(_truncated_jpeg_bytes())

        assert result is not None
        assert result["diagnosis"] == "Early blight"
