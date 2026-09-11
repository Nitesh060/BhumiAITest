"""Regression tests: missing signals must not become a confident recommendation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crop_recommendation import MIN_SIGNALS_REQUIRED, recommend_crop
from gemini_service import _format_farm_context

FULL = dict(ndvi=0.65, ndmi=0.30, rainfall=7.0, temperature=29.0, groundwater=200.0)


def test_no_signals_returns_no_recommendation():
    """Every input was coerced with `x or 0`, so all-missing data fell
    through the dry-condition branches and returned "Groundnut, 35%" —
    byte-identical to what a genuinely analysed poor farm scores."""
    result = recommend_crop(None, None, None, None, None)
    assert result["available"] is False
    assert result["primary"] is None
    assert "not enough data" in result["reason"]


def test_below_the_threshold_returns_no_recommendation():
    assert recommend_crop(0.6, 0.3, None, None, None)["available"] is False


def test_at_the_threshold_a_recommendation_is_made():
    result = recommend_crop(0.6, 0.3, 6.0, None, None)
    assert result["available"] is True
    assert result["signals_available"] == MIN_SIGNALS_REQUIRED


def test_full_data_still_recommends_as_before():
    result = recommend_crop(**FULL)
    assert result["available"] is True
    assert result["primary"]["crop"] == "Rice"
    assert result["secondary"] is not None


def test_all_five_core_signals_are_reported():
    used = recommend_crop(**FULL)["signals_used"]
    for key in ("ndvi", "ndmi", "rainfall", "temperature", "groundwater", "evi", "ndre"):
        assert key in used, f"{key} missing from signals_used"


def test_consumers_survive_a_null_primary():
    """`.get("primary", {})` only falls back when the KEY is absent, so an
    explicit None reached `.get('crop')` and raised AttributeError."""
    context = {
        "score": 553, "grade": "Poor", "components": {},
        "recommended_crops": recommend_crop(None, None, None, None, None),
        "climate_risk": {},
    }
    assert "not available" in _format_farm_context(context)
