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

# A season needs at least this many real parameters before its score means
# anything. Below the threshold the season is treated as "no data" and gets
# the floor below instead of a score derived from one or two stray values.
# Without this guard a season holding a single parameter scored that one
# parameter's normalised value as the WHOLE season — routinely 400/400.
MIN_SEASON_PARAMETERS = 3

# Score given to a season with no usable data — matching the reference
# report, where every "no crop grown / not classified" season is booked at
# exactly 200/400 and still counts toward the 1000-point denominator. It is
# a floor, not an exclusion: a farm that grows nothing in Rabi must score
# below an otherwise identical farm that does.
NO_DATA_SEASON_FLOOR_PCT = 50.0

# The overall score is the raw Base + Kharif + Rabi total on its own
# 0-1000 scale, and the grade bands are read off that same scale. The
# previous code rescaled the raw total onto 400-1000 FIRST and then applied
# bands that were already defined for 0-1000, so every farm was lifted by
# the 400-point floor and compressed into the top bands.
SCORE_MIN = 0
SCORE_MAX = 1000


def _season_window(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Surface the season's own window alongside its score.

    `latest_season_windows()` computes a `complete` flag for each season but
    nothing consumed it, so a Kharif that was ten days old scored exactly
    like a harvested one and the report gave no hint which it was. Carrying
    it here lets the UI/PDF label an in-progress season, and drops the
    confidence rather than presenting partial-canopy data as a final result.
    """
    meta = meta or {}
    complete = meta.get("complete")
    return {
        "season_label": meta.get("label"),
        "window_start": meta.get("start"),
        "window_end": meta.get("end"),
        "season_complete": complete,
        "in_progress": complete is False,
    }


def _grade_out_of(score: Optional[float], max_score: float) -> str:
    """Grade a sub-score (e.g. 278 out of 400) by projecting it onto the
    same 0-1000 scale the bands are defined on. 278/400 -> 695 -> "Fair".

    The old code did `400 + (score / max) * 600` first, which squeezed the
    whole range into 400-1000 and then compared it against bands already
    written for 0-1000 — so 237/400 came out "Good" instead of "Poor".
    """
    if score is None or not max_score:
        return "No data"
    return assign_grade(score / max_score * SCORE_MAX)


def compute_base_score(irrigation: Optional[Dict[str, Any]], cropping_intensity: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """0-200. Mirrors SatSource's own stated Base Score definition
    ("a combination of cropping intensity and irrigation mapping" —
    their own glossary page) using this app's existing signals:
      - enrichment_service.fetch_irrigation_signal() -> likely_irrigated
      - enrichment_service.fetch_cropping_intensity() -> label
        ("Single cropping (mono)" / "Double cropping" / "Triple / multi
        cropping" — the label this function ACTUALLY returns; a prior
        version of this map used made-up labels ("Once a Year" / "Twice
        a Year" / "No Crop Grown") that fetch_cropping_intensity never
        produces, so the cropping-intensity signal silently never
        matched and Base Score was computed from irrigation alone.
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
    intensity_map = {"Triple / multi cropping": 100.0, "Double cropping": 70.0, "Single cropping (mono)": 40.0}
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
        "grade": _grade_out_of(base_score, 200),
        "irrigation_condition": "Irrigated" if irrigation.get("likely_irrigated") else ("Rainfed" if irrigation.get("likely_irrigated") is False else "Not available"),
        "cropping_intensity": intensity_label or "Not available",
        "data_available": base_score is not None,
    }


def _compute_season_subscore(raw_values: Optional[Dict[str, Optional[float]]], weights: Optional[Dict[str, float]], max_score: int, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Runs the full 20-parameter comprehensive-score formula against
    ONE season's raw values only, then scales its 0-100 result onto
    this season's share of the overall 1000-point scale (400 for
    Kharif/Rabi)."""
    comp_result = compute_comprehensive_score(raw_values or {}, weights=weights)
    score_0_100 = comp_result.get("score_0_100")
    used = comp_result.get("parameters_used", 0)

    # Too little signal to score this season honestly. Fall back to the
    # no-data floor rather than either (a) scoring one stray parameter as
    # the entire season, or (b) dropping the season out of the total.
    if score_0_100 is None or used < MIN_SEASON_PARAMETERS:
        floored = round(NO_DATA_SEASON_FLOOR_PCT / 100 * max_score)
        return {
            "score": floored,
            "max_score": max_score,
            "grade": _grade_out_of(floored, max_score),
            "data_available": False,
            "scored_from": "no-data floor",
            "parameters_used": used,
            "parameters_total": comp_result.get("parameters_total", 20),
            "components": adapt_components(comp_result),
            **_season_window(meta),
        }

    scaled = round(score_0_100 / 100 * max_score)
    return {
        "score": scaled,
        "max_score": max_score,
        "grade": _grade_out_of(scaled, max_score),
        "data_available": True,
        "scored_from": "observed",
        "parameters_used": used,
        "parameters_total": comp_result["parameters_total"],
        "components": adapt_components(comp_result),
        **_season_window(meta),
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
    kharif_meta: Optional[Dict[str, Any]] = None,
    rabi_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Top-level entry point — the ONE Bhumi AI FarmScore. Combines
    Base + Kharif + Rabi into a single 0-1000 score, read off the same
    scale the grade bands are defined on. Never raises; if EVERYTHING is
    missing it returns the bottom of the scale rather than a fabricated
    high score.
    """
    base = compute_base_score(irrigation, cropping_intensity)
    kharif = _compute_season_subscore(kharif_raw_values, weights, 400, kharif_meta)
    rabi = _compute_season_subscore(rabi_raw_values, weights, 400, rabi_meta)

    components_available = [c for c in (base, kharif, rabi) if c.get("data_available")]
    if not components_available:
        return {
            "final_score": SCORE_MIN,
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

    # Kharif and Rabi are always present now — a season with no signal
    # carries the no-data floor rather than dropping out — so the
    # denominator stays at the full 1000 whenever Base is available, and
    # the reported score IS the raw Base + Kharif + Rabi total.
    scored = [c for c in (base, kharif, rabi) if c.get("score") is not None]
    raw_total = sum(c["score"] for c in scored)
    max_possible = sum(c["max_score"] for c in scored)

    # Base is the only component that can still go missing entirely (no
    # irrigation and no cropping-intensity signal). In that case project
    # what was scored onto the full 1000 rather than adding a floor of 400.
    final_score = round(raw_total / max_possible * SCORE_MAX) if max_possible else SCORE_MIN
    grade = assign_grade(final_score)

    merged_components = _merge_season_components(kharif["components"], rabi["components"])
    parameters_used = sum(1 for c in merged_components.values() if c["data_available"])

    return {
        "final_score": final_score,
        "grade": grade,
        "components": merged_components,
        "parameters_used": parameters_used,
        "parameters_total": len(PARAMETER_LABELS),
        # An in-progress season is real data but not a finished result, so it
        # caps confidence no matter how many parameters came back.
        "confidence": ("low" if any(c.get("in_progress") for c in (kharif, rabi))
                       else "high" if parameters_used >= 15
                       else "moderate" if parameters_used >= 10 else "low"),
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
