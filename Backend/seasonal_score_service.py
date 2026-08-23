"""
seasonal_score_service.py
===========================
The main Bhumi AI FarmScore — a Base + Average Kharif + Average Rabi
composite, in the spirit of SatSource's SatScore report layout (Base
Score + Average Kharif Score + Average Rabi Score => Overall score with
a grade/risk band). This is now THE ONE FarmScore the app shows —
there is deliberately no second, separate "seasonal" score anymore.

Score structure
----------------
  Base Score      0-200   irrigation condition + cropping intensity
                          (compute_base_score, unchanged since this
                          module's earlier "Bhumi Seasonal Score" days)
  Kharif Score    0-400   the SAME transparent 20-parameter suitability
                          formula used everywhere else in this app
                          (comprehensive_score_service.compute_comprehensive_score),
                          computed using ONLY the latest Kharif season's
                          satellite/weather values
  Rabi Score      0-400   identical, using ONLY the latest Rabi season's
                          values
  ------------------------------------------------------------------
  Raw total       0-1000  Base + Kharif + Rabi
  Final score   400-1000  Raw total rescaled onto the same 400-1000
                          display range used across the rest of the app
                          (matching the SatSure-style grade bands in
                          comprehensive_score_service.GRADE_BANDS)

All 20 satellite/weather parameters (NDVI, EVI, SAVI, MSAVI, NDRE,
NDMI, NDWI, CI_Green, CI_RedEdge, VV, VH, VH/VV, RVI, rainfall,
air_temp, solar_radiation, SPI, SPEI, GDD, LST) are sub-parameters of
the Kharif and Rabi scores above — there is no flat, season-blind
20-parameter score computed separately from this anymore. Irrigation
and cropping intensity are the two Base Score inputs.

Missing components are excluded from the total (not scored as 0) and
the raw total is rescaled against whatever max was actually achieved,
so a farm with (for example) no Rabi signal still gets a final score
comparable to one with all three components available.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from comprehensive_score_service import (
    DEFAULT_GRADE,
    PARAMETER_GROUPS,
    PARAMETER_LABELS,
    assign_grade,
    compute_comprehensive_score,
)
from scoring import SOURCES, UNITS, adapt_components

logger = logging.getLogger(__name__)

# Same 5-tier bands as comprehensive_score_service.GRADE_BANDS, with a
# risk-rating label for each — matches the reference SatSource-style
# report's own "risk rating" column.
RISK_RATING_BY_GRADE = {
    "Excellent": "Lowest",
    "Very Good": "Low",
    "Good": "Medium",
    "Fair": "High",
    "Poor": "Highest",
}


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
        "grade": assign_grade(round(400 + base_pct * 6)) if base_pct is not None else "No data",
        "irrigation_condition": "Irrigated" if irrigation.get("likely_irrigated") else ("Rainfed" if irrigation.get("likely_irrigated") is False else "Not available"),
        "cropping_intensity": intensity_label or "Not available",
        "data_available": base_score is not None,
    }


def _compute_season_subscore(raw_values: Optional[Dict[str, Optional[float]]], weights: Optional[Dict[str, float]], max_score: int) -> Dict[str, Any]:
    """Runs the full 20-parameter comprehensive-score formula against
    ONE season's raw values only, then scales its 0-100 result onto
    this season's share of the overall 1000-point scale (400 for
    Kharif/Rabi)."""
    comp_result = compute_comprehensive_score(raw_values or {}, weights=weights)
    score_0_100 = comp_result.get("score_0_100")
    if score_0_100 is None:
        return {
            "score": None, "max_score": max_score, "grade": "No data", "data_available": False,
            "parameters_used": 0, "parameters_total": comp_result.get("parameters_total", 20),
            "components": adapt_components(comp_result),
        }
    scaled = round(score_0_100 / 100 * max_score)
    # Grade this season's own contribution on the same 400-1000 band
    # scale, purely for the per-factor display (e.g. "Good (312/400)")
    # — not part of the overall score's own grading.
    equivalent_400_1000 = round(400 + (scaled / max_score) * 600)
    return {
        "score": scaled, "max_score": max_score, "grade": assign_grade(equivalent_400_1000), "data_available": True,
        "parameters_used": comp_result["parameters_used"], "parameters_total": comp_result["parameters_total"],
        "components": adapt_components(comp_result),
    }


def _merge_season_components(kharif_components: Dict[str, Any], rabi_components: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible flat components dict — one entry per
    parameter with a top-level raw_value/sub_score (averaged across
    whichever of Kharif/Rabi has data) plus nested "kharif"/"rabi"
    breakdowns for anything that wants the per-season detail."""
    merged = {}
    for key in PARAMETER_LABELS:
        k_c = kharif_components.get(key)
        r_c = rabi_components.get(key)
        raw_vals = [c["raw_value"] for c in (k_c, r_c) if c and c.get("raw_value") is not None]
        sub_scores = [c["sub_score"] for c in (k_c, r_c) if c and c.get("sub_score") is not None]
        weight = (k_c or r_c or {}).get("weight")
        merged[key] = {
            "label": PARAMETER_LABELS.get(key, key),
            "unit": UNITS.get(key, ""),
            "source": SOURCES.get(key, ""),
            "weight": weight,
            "raw_value": round(sum(raw_vals) / len(raw_vals), 4) if raw_vals else None,
            "sub_score": round(sum(sub_scores) / len(sub_scores), 2) if sub_scores else None,
            "data_available": bool(sub_scores),
            "kharif": k_c,
            "rabi": r_c,
        }
    return merged


def compute_farmscore(
    irrigation: Optional[Dict[str, Any]],
    cropping_intensity: Optional[Dict[str, Any]],
    kharif_raw_values: Optional[Dict[str, Optional[float]]],
    rabi_raw_values: Optional[Dict[str, Optional[float]]],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Top-level entry point — the ONE Bhumi AI FarmScore. Combines
    Base + Kharif + Rabi into a single 400-1000 score. Never raises; a
    completely missing input just yields "No data" sub-components and,
    if EVERYTHING is missing, a default Poor-grade result rather than a
    fabricated high score.
    """
    base = compute_base_score(irrigation, cropping_intensity)
    kharif = _compute_season_subscore(kharif_raw_values, weights, 400)
    rabi = _compute_season_subscore(rabi_raw_values, weights, 400)

    components_available = [c for c in (base, kharif, rabi) if c.get("data_available")]
    if not components_available:
        return {
            "final_score": 400,
            "grade": DEFAULT_GRADE,
            "components": _merge_season_components(kharif["components"], rabi["components"]),
            "parameters_used": 0,
            "parameters_total": len(PARAMETER_LABELS),
            "confidence": "low",
            "parameter_groups": PARAMETER_GROUPS,
            "breakdown": {
                "available": False,
                "reason": "No irrigation, cropping-intensity, or seasonal satellite/weather signal available for this location.",
                "base": base, "kharif": kharif, "rabi": rabi,
            },
        }

    # Missing components are excluded (not scored as 0) — partial data
    # still produces a usable, honestly-labeled result.
    raw_total = sum(c["score"] for c in components_available)
    max_possible = sum(c["max_score"] for c in components_available)
    final_score = round(400 + (raw_total / max_possible) * 600) if max_possible else 400
    grade = assign_grade(final_score)

    merged_components = _merge_season_components(kharif["components"], rabi["components"])
    parameters_used = sum(1 for c in merged_components.values() if c["data_available"])

    return {
        "final_score": final_score,
        "grade": grade,
        "components": merged_components,
        "parameters_used": parameters_used,
        "parameters_total": len(PARAMETER_LABELS),
        "confidence": "high" if parameters_used >= 15 else "moderate" if parameters_used >= 10 else "low",
        "parameter_groups": PARAMETER_GROUPS,
        "validation_status": "provisional — Base+Kharif+Rabi weighting is a documented starting point, not empirically calibrated against ground-truth farm outcomes yet",
        "breakdown": {
            "available": True,
            "overall_score": final_score,
            "category": grade,
            "risk_rating": RISK_RATING_BY_GRADE.get(grade, "Highest"),
            "base": base,
            "kharif": kharif,
            "rabi": rabi,
            "components_used": len(components_available),
            "components_total": 3,
            "method": "Bhumi AI FarmScore — Base (irrigation + cropping intensity, 0-200) + Average Kharif Score (0-400) + "
                      "Average Rabi Score (0-400), each of the latter two computed with the same transparent 20-parameter "
                      "suitability formula scoped to that season's own satellite/weather data. Rescaled to a 400-1000 "
                      "final score. Missing components are excluded and the total rescaled against whatever max was "
                      "actually achieved, rather than scored as zero.",
        },
    }
