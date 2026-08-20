"""
seasonal_score_service.py
===========================
Bhumi Seasonal Score — a Base + Kharif + Rabi composite, in the spirit
of SatSource's SatScore report layout (Base Score + Average Kharif
Score + Average Rabi Score => Overall score with a risk-rating band).

HONESTY NOTE — read before wiring this into a lending decision:
This is a NEW, independent, formula-based proxy built entirely from
signals this app already computes from open satellite data (Sentinel-1/2
irrigation + cropping-intensity signals, multi-year Kharif/Rabi NDVI).
It is NOT the same computation SatSource runs, does not use SatSource's
methodology or thresholds, and is not validated against real harvested-
yield ground truth. Treat it exactly like yield_prediction.py's yield
estimate: a transparent, documented starting point — replace the NDVI-
to-score curve with a regression trained on YOUR farms' real seasonal
performance the moment that data exists.

This is DELIBERATELY a separate, complementary score from the main
FarmScore (300-900, in scoring.py / comprehensive_score_service.py).
FarmScore measures current land-condition SUITABILITY across 20
weather/vegetation/radar parameters. Bhumi Seasonal Score measures
multi-year SEASONAL CROP-PERFORMANCE PATTERN — a different question
("has this land reliably produced a crop, season after season?" vs
"is this land in good condition right now?"). Show both, don't merge
them into one number.

Score structure
----------------
  Base Score      0-200   irrigation condition + cropping intensity
  Kharif Score    0-400   average of each available Kharif season's
                          in-season NDVI, scored against a reference
                          curve, across the lookback window
  Rabi Score      0-400   same, for Rabi seasons
  ------------------------------------------------------------------
  Overall         0-1000  Base + Kharif + Rabi

Band cuts (independently chosen for Bhumi's own 0-1000 scale — not
copied from any other product's thresholds):
  0-399    Poor       Highest risk
  400-549  Fair       High risk
  550-699  Good       Medium risk
  700-849  Very Good  Low risk
  850-1000 Excellent  Lowest risk
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# NDVI reference curve for scoring an in-season NDVI value onto a 0-100
# sub-scale before it's blended into the 0-400 seasonal score. These are
# deliberately generous, generic vegetation-health cutoffs (not crop-
# specific) since at this stage the season's crop identity is itself
# only a heuristic guess (see crop_intelligence_service.identify_crop_history).
SEASON_NDVI_FLOOR = 0.20   # at/below this: essentially no active crop signal
SEASON_NDVI_CEILING = 0.75  # at/above this: full marks — dense, healthy canopy


def _ndvi_to_subscore_0_100(ndvi: Optional[float]) -> Optional[float]:
    if ndvi is None:
        return None
    if ndvi <= SEASON_NDVI_FLOOR:
        return 0.0
    if ndvi >= SEASON_NDVI_CEILING:
        return 100.0
    return round((ndvi - SEASON_NDVI_FLOOR) / (SEASON_NDVI_CEILING - SEASON_NDVI_FLOOR) * 100, 1)


def _grade_for_subscore(pct: Optional[float]) -> str:
    if pct is None:
        return "No data"
    if pct < 30:
        return "Poor"
    if pct < 55:
        return "Fair"
    if pct < 75:
        return "Good"
    if pct < 90:
        return "Very Good"
    return "Excellent"


def compute_base_score(irrigation: Optional[Dict[str, Any]], cropping_intensity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """0-200. Mirrors SatSource's own stated Base Score definition
    ("a combination of cropping intensity and irrigation mapping" —
    their own glossary page) using this app's existing signals:
      - enrichment_service.fetch_irrigation_signal() -> likely_irrigated
      - enrichment_service.fetch_cropping_intensity() -> label
        ("Once a Year" / "Twice a Year" / "No Crop Grown")
    """
    irrigation = irrigation or {}
    cropping_intensity = cropping_intensity or {}

    irrigation_pts = 0.0
    irrigation_available = irrigation.get("likely_irrigated") is not None
    if irrigation.get("likely_irrigated") is True:
        irrigation_pts = 100.0
    elif irrigation.get("likely_irrigated") is False:
        irrigation_pts = 40.0  # rainfed still scores something — not zero

    intensity_label = cropping_intensity.get("label")
    intensity_map = {"Twice a Year": 100.0, "Once a Year": 60.0, "No Crop Grown": 10.0}
    intensity_pts = intensity_map.get(intensity_label)
    intensity_available = intensity_pts is not None
    if intensity_pts is None:
        intensity_pts = 0.0

    available_parts = [p for p, ok in ((irrigation_pts, irrigation_available), (intensity_pts, intensity_available)) if ok]
    base_pct = sum(available_parts) / len(available_parts) if available_parts else None
    base_score = round(base_pct / 100 * 200) if base_pct is not None else None

    return {
        "score": base_score,
        "max_score": 200,
        "grade": _grade_for_subscore(base_pct),
        "irrigation_condition": "Irrigated" if irrigation.get("likely_irrigated") else ("Rainfed" if irrigation.get("likely_irrigated") is False else "Not available"),
        "cropping_intensity": intensity_label or "Not available",
        "data_available": base_score is not None,
    }


def _season_score_from_ndvi_values(ndvi_values: List[float]) -> Dict[str, Any]:
    """Blends however many valid seasonal NDVI readings are available
    (e.g. 2 of the last 3 Kharif seasons) into one 0-400 seasonal
    score. Missing seasons are excluded from the average, never
    counted as zero — one bad-data year shouldn't tank the score.
    """
    subscores = [s for s in (_ndvi_to_subscore_0_100(v) for v in ndvi_values) if s is not None]
    if not subscores:
        return {"score": None, "max_score": 400, "grade": "No data", "years_used": 0, "data_available": False}
    pct = sum(subscores) / len(subscores)
    return {
        "score": round(pct / 100 * 400),
        "max_score": 400,
        "grade": _grade_for_subscore(pct),
        "years_used": len(subscores),
        "data_available": True,
    }


def compute_seasonal_scores_from_history(cropping_history: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Takes the {"years": [{"year", "kharif": {"ndvi", "cropped"}, "rabi": {...}}]}
    shape from enrichment_service.fetch_cropping_history() (already
    fetched elsewhere in the request — no extra Earth Engine call here)
    and produces the Kharif and Rabi seasonal scores.
    """
    years = (cropping_history or {}).get("years") or []
    kharif_vals = [y["kharif"]["ndvi"] for y in years if y.get("kharif", {}).get("ndvi") is not None]
    rabi_vals = [y["rabi"]["ndvi"] for y in years if y.get("rabi", {}).get("ndvi") is not None]

    return {
        "kharif": _season_score_from_ndvi_values(kharif_vals),
        "rabi": _season_score_from_ndvi_values(rabi_vals),
        "years_in_window": len(years),
    }


OVERALL_BANDS = [
    (0, 399, "Poor", "Highest"),
    (400, 549, "Fair", "High"),
    (550, 699, "Good", "Medium"),
    (700, 849, "Very Good", "Low"),
    (850, 1000, "Excellent", "Lowest"),
]


def _overall_band(score: int) -> Dict[str, str]:
    for lo, hi, category, risk in OVERALL_BANDS:
        if lo <= score <= hi:
            return {"category": category, "risk_rating": risk}
    return {"category": "Poor", "risk_rating": "Highest"}


def compute_seasonal_performance_score(
    irrigation: Optional[Dict[str, Any]],
    cropping_intensity: Optional[Dict[str, Any]],
    cropping_history: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Top-level entry point — combine base + kharif + rabi into the
    overall Bhumi Seasonal Score. Never raises; a completely missing
    input just yields "No data" sub-components and, if EVERYTHING is
    missing, an unavailable overall result rather than a fabricated 0.
    """
    base = compute_base_score(irrigation, cropping_intensity)
    seasonal = compute_seasonal_scores_from_history(cropping_history)
    kharif, rabi = seasonal["kharif"], seasonal["rabi"]

    components_available = [c for c in (base, kharif, rabi) if c.get("data_available")]
    if not components_available:
        return {
            "available": False,
            "reason": "No irrigation, cropping-intensity, or historical NDVI signal available for this location.",
            "base": base, "kharif": kharif, "rabi": rabi,
        }

    # Missing components are excluded (not scored as 0) — same
    # philosophy as the rest of this app's scoring: partial data still
    # produces a usable, honestly-labeled result.
    overall = sum(c["score"] for c in components_available)
    max_possible = sum(c["max_score"] for c in components_available)
    # Scale up to the full 0-1000 if a component was unavailable, so a
    # 2-of-3-components score is still comparable to a full 3-of-3 one.
    scaled_overall = round(overall / max_possible * 1000) if max_possible else 0
    band = _overall_band(scaled_overall)

    return {
        "available": True,
        "overall_score": scaled_overall,
        "max_score": 1000,
        "category": band["category"],
        "risk_rating": band["risk_rating"],
        "base": base,
        "kharif": kharif,
        "rabi": rabi,
        "components_used": len(components_available),
        "components_total": 3,
        "method": "Bhumi Seasonal Score — Base (irrigation + cropping intensity) + Kharif + Rabi NDVI-derived seasonal scores. "
                  "An independent, formula-based proxy for multi-year seasonal crop-performance pattern — "
                  "NOT validated against real harvested-yield ground truth. Distinct from the main FarmScore "
                  "(current land-condition suitability); the two measure different things and are shown separately.",
    }
