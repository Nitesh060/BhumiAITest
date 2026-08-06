"""
crop_intelligence_service.py
==============================
Phase 3 — Crop Intelligence.

HONESTY NOTE: "Crop Identification" here is a rule-based heuristic
(NDVI seasonal shape + flooding/water signature), NOT a trained ML
classifier. A real crop-ID model needs thousands of labeled
farm-season examples this app doesn't have. This module is upfront
about that in every response — see the `confidence`/`method` fields.

Reuses:
  - enrichment_service.fetch_cropping_intensity() for the 12-month
    NDVI curve (avoids a duplicate Earth Engine call)
  - enrichment_service.fetch_cropping_history() for the 3-year
    Kharif/Rabi cropped/fallow signal
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean

logger = logging.getLogger(__name__)

# Static reference — India-general Kharif/Rabi sowing & harvest windows.
# Indicative (ICAR-style), NOT farm-specific or state-calibrated.
CROP_CALENDAR = {
    "Rice": {
        "kharif": {"sow": "Jun 15 – Jul 15", "harvest": "Oct 15 – Nov 30", "duration_days": 120},
        "rabi": {"sow": "Nov 15 – Dec 15", "harvest": "Mar 15 – Apr 15", "duration_days": 120},
    },
    "Wheat": {
        "rabi": {"sow": "Nov 1 – Nov 30", "harvest": "Mar 15 – Apr 15", "duration_days": 135},
    },
    "Maize": {
        "kharif": {"sow": "Jun 15 – Jul 15", "harvest": "Sep 15 – Oct 15", "duration_days": 95},
        "rabi": {"sow": "Oct 15 – Nov 15", "harvest": "Feb 15 – Mar 15", "duration_days": 110},
    },
    "Groundnut": {
        "kharif": {"sow": "Jun 15 – Jul 15", "harvest": "Sep 30 – Oct 31", "duration_days": 100},
    },
}


# ---------------------------------------------------------------------------
# Crop Identification — heuristic, not ML
# ---------------------------------------------------------------------------

def identify_crop_heuristic(lat: float, lng: float, polygon: Optional[dict] = None,
                             monthly_ndvi: Optional[List[Optional[float]]] = None) -> Dict[str, Any]:
    """Guesses the most likely currently-grown crop from a small set of
    known crops (Rice, Wheat, Maize, Groundnut — matching
    crop_recommendation.py) using two signals:
      1. Which season the NDVI peak falls in (Kharif Jun-Oct vs Rabi Nov-Apr)
      2. Whether an early-season flooding/water signature is present
         (a strong tell for paddy specifically — NDWI > 0 in the first
         6-8 weeks after the Kharif peak begins)
    This is NOT species-level computer vision — it's a shape-matching
    heuristic and should be labeled as such wherever shown.
    """
    region = _get_region(lat, lng, polygon)

    if monthly_ndvi is None:
        monthly_ndvi = _fetch_monthly_ndvi(region)

    clean = [(i, v) for i, v in enumerate(monthly_ndvi) if v is not None]
    if not clean:
        return {"identified_crop": None, "confidence": "none", "reason": "Insufficient NDVI data"}

    peak_month_idx, peak_val = max(clean, key=lambda x: x[1])
    peak_month = peak_month_idx + 1  # 1-12

    is_kharif_peak = 8 <= peak_month <= 11   # NDVI peaks Aug-Nov for a Jun-Jul kharif sowing
    is_rabi_peak = peak_month in (1, 2, 3)    # NDVI peaks Jan-Mar for a Nov rabi sowing

    flood_signal = _check_early_season_flooding(region, kharif=is_kharif_peak)

    if is_kharif_peak and flood_signal:
        crop, confidence = "Rice", "moderate"
    elif is_kharif_peak and not flood_signal:
        crop, confidence = "Maize", "low"  # could also be Groundnut — can't distinguish reliably by NDVI shape alone
    elif is_rabi_peak:
        crop, confidence = "Wheat", "low"
    else:
        crop, confidence = None, "none"

    return {
        "identified_crop": crop,
        "confidence": confidence,
        "peak_ndvi_month": peak_month,
        "peak_ndvi": round(peak_val, 4),
        "flood_signature_detected": flood_signal,
        "method": "NDVI seasonal-peak timing + early-season flood signature — a heuristic, not a trained crop-classification model.",
        "note": "Confidence is capped at 'moderate' because this app has no ground-truth labels to validate against. Treat as a starting hypothesis, not a confirmed identification.",
    }


def _fetch_monthly_ndvi(region) -> List[Optional[float]]:
    monthly = []
    for m in range(1, 13):
        start = f"2023-{m:02d}-01"
        end_month, end_year = (m + 1, 2023) if m < 12 else (1, 2024)
        end = f"{end_year}-{end_month:02d}-01"
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(start, end)
            .filterBounds(region)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )
        ndvi_img = s2.map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")).select("NDVI").mean()
        val = _reduce_mean(ndvi_img, region, scale=20)
        monthly.append(round(val, 4) if val is not None else None)
    return monthly


def _check_early_season_flooding(region, kharif: bool) -> bool:
    """NDWI > 0 in the early Kharif window (Jun 15 - Aug 15) is a
    reasonable tell for a flooded paddy field at transplanting.
    """
    if not kharif:
        return False
    try:
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate("2023-06-15", "2023-08-15")
            .filterBounds(region)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )
        ndwi_img = s2.map(lambda img: img.normalizedDifference(["B3", "B8"]).rename("NDWI")).select("NDWI").mean()
        val = _reduce_mean(ndwi_img, region, scale=20)
        return val is not None and val > 0
    except Exception:
        logger.exception("Flood-signature check failed (non-fatal)")
        return False


# ---------------------------------------------------------------------------
# Growth Stage Detection
# ---------------------------------------------------------------------------

def detect_growth_stage(monthly_ndvi: List[Optional[float]], current_month: int) -> Dict[str, Any]:
    """Classifies growth stage from where the current month's NDVI sits
    relative to the season's own peak — Emergence (rising, <40% of
    peak), Vegetative (rising, 40-80%), Flowering/Peak (>80% of peak),
    Maturity (falling, 40-80% of peak, after the peak month), Harvested/
    Fallow (falling, <40%).
    """
    clean = [(i, v) for i, v in enumerate(monthly_ndvi) if v is not None]
    if not clean or monthly_ndvi[current_month - 1] is None:
        return {"stage": None, "reason": "Insufficient NDVI data for current month"}

    peak_idx, peak_val = max(clean, key=lambda x: x[1])
    current_val = monthly_ndvi[current_month - 1]
    current_idx = current_month - 1

    if peak_val <= 0:
        return {"stage": "Fallow / no active crop", "current_ndvi": current_val, "peak_ndvi": peak_val}

    ratio = current_val / peak_val
    before_peak = current_idx <= peak_idx

    if ratio < 0.35:
        stage = "Emergence / early vegetative" if before_peak else "Harvested / fallow"
    elif ratio < 0.75:
        stage = "Vegetative growth" if before_peak else "Maturity / senescence"
    else:
        stage = "Peak vegetative / flowering"

    return {
        "stage": stage,
        "current_ndvi": current_val,
        "peak_ndvi": peak_val,
        "ratio_to_peak": round(ratio, 2),
        "method": "NDVI position relative to this season's own peak — indicative, not a phenology-model prediction.",
    }


# ---------------------------------------------------------------------------
# Sowing & Harvest Prediction
# ---------------------------------------------------------------------------

def estimate_sowing_harvest(monthly_ndvi: List[Optional[float]], identified_crop: Optional[str],
                             season: str = "kharif") -> Dict[str, Any]:
    """Finds the month NDVI first crosses 30% of the season's peak
    (a sowing/emergence proxy) and projects harvest using the crop's
    typical duration from CROP_CALENDAR — falls back to the static
    calendar entirely if the NDVI signal is too weak to detect a clear
    rise.
    """
    calendar_entry = CROP_CALENDAR.get(identified_crop, {}).get(season) if identified_crop else None

    clean = [(i, v) for i, v in enumerate(monthly_ndvi) if v is not None]
    if not clean:
        return {"sowing_estimate": None, "harvest_estimate": None, "source": "calendar_only", "calendar_reference": calendar_entry}

    peak_idx, peak_val = max(clean, key=lambda x: x[1])
    threshold = peak_val * 0.3

    sowing_month_idx = None
    for i, v in clean:
        if i <= peak_idx and v >= threshold:
            sowing_month_idx = i
            break

    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    sowing_estimate = month_names[sowing_month_idx] if sowing_month_idx is not None else None

    harvest_estimate = None
    if sowing_month_idx is not None and calendar_entry:
        duration_months = round(calendar_entry["duration_days"] / 30)
        harvest_month_idx = (sowing_month_idx + duration_months) % 12
        harvest_estimate = month_names[harvest_month_idx]

    return {
        "sowing_estimate_month": sowing_estimate,
        "harvest_estimate_month": harvest_estimate,
        "source": "ndvi_detected_onset + calendar_duration" if sowing_month_idx is not None else "calendar_only",
        "calendar_reference": calendar_entry,
        "note": "Sowing month is inferred from NDVI rise onset; harvest is that date plus the crop's typical duration — not a direct satellite observation of harvest.",
    }


# ---------------------------------------------------------------------------
# Crop Rotation — reuses the existing 3-year Kharif/Rabi cropped/fallow
# signal (enrichment_service.fetch_cropping_history) and adds a coarse
# per-season crop-type guess to describe the rotation pattern.
# ---------------------------------------------------------------------------

def detect_crop_rotation(cropping_history: Dict[str, Any]) -> Dict[str, Any]:
    """cropping_history is the dict already returned by
    enrichment_service.fetch_cropping_history() — this function doesn't
    re-fetch satellite data, just interprets what's already there.
    """
    years = cropping_history.get("years", []) if cropping_history else []
    if not years:
        return {"pattern": None, "reason": "No cropping history available"}

    pattern_rows = []
    for y in years:
        kharif_cropped = y.get("kharif", {}).get("cropped", False)
        rabi_cropped = y.get("rabi", {}).get("cropped", False)
        pattern_rows.append({
            "year": y["year"],
            "kharif": "Cropped" if kharif_cropped else "Fallow",
            "rabi": "Cropped" if rabi_cropped else "Fallow",
        })

    both_seasons_years = sum(1 for r in pattern_rows if r["kharif"] == "Cropped" and r["rabi"] == "Cropped")
    kharif_only_years = sum(1 for r in pattern_rows if r["kharif"] == "Cropped" and r["rabi"] == "Fallow")

    if both_seasons_years == len(pattern_rows):
        summary = "Double-cropped every year in the 3-year window — consistent Kharif+Rabi rotation."
    elif both_seasons_years > 0:
        summary = f"Double-cropped in {both_seasons_years} of {len(pattern_rows)} years — inconsistent rotation."
    elif kharif_only_years == len(pattern_rows):
        summary = "Single (Kharif-only) cropping every year — no Rabi rotation detected."
    else:
        summary = "Irregular cropping pattern across the 3-year window."

    return {
        "years": pattern_rows,
        "summary": summary,
        "note": "Season-level cropped/fallow only — does not identify which specific crop was grown each season (see Crop Identification for the current season's best guess).",
    }
