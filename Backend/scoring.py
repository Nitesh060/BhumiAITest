"""
scoring.py
==========
Main FarmScore (300-900 scale) — now computed from the full 20-parameter
comprehensive model (Vegetation + Radar + Weather + Temperature)
instead of the original 5-parameter model (NDVI/NDMI/Rainfall/
Temperature/Groundwater).

Groundwater is INTENTIONALLY not part of this score (explicit choice —
the 20-parameter set replaces it entirely, not additively). Delegates
all normalization/weighting math to comprehensive_score_service.py so
there is exactly one place that logic lives — this module is a thin
adapter that (a) accepts the raw values this app's callers already
fetch, and (b) reshapes the output into the component format the rest
of the app (PDF report, WhatsApp summary, Dashboard breakdown) expects.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from comprehensive_score_service import (
    compute_comprehensive_score,
    PARAMETER_LABELS,
    DEFAULT_GRADE,
)

logger = logging.getLogger(__name__)

# Unit + source shown per parameter in the Dashboard/PDF/WhatsApp
# breakdown — purely cosmetic metadata, not used in the scoring math.
_UNITS = {
    "ndvi": "", "evi": "", "savi": "", "msavi": "", "ndre": "", "ndmi": "", "ndwi": "",
    "ci_green": "", "ci_rededge": "",
    "vv": " dB", "vh": " dB", "vh_vv": "", "rvi": "",
    "rainfall": " mm/day", "air_temp": "°C", "solar_radiation": " MJ/m²/day",
    "spi": "", "spei": "", "gdd": " GDD-units", "lst": "°C",
}
_SOURCES = {
    "ndvi": "Sentinel-2", "evi": "Sentinel-2", "savi": "Sentinel-2", "msavi": "Sentinel-2",
    "ndre": "Sentinel-2 Red Edge", "ndmi": "Sentinel-2", "ndwi": "Sentinel-2",
    "ci_green": "Sentinel-2", "ci_rededge": "Sentinel-2",
    "vv": "Sentinel-1 SAR", "vh": "Sentinel-1 SAR", "vh_vv": "Sentinel-1 SAR", "rvi": "Sentinel-1 SAR",
    "rainfall": "CHIRPS", "air_temp": "MODIS LST", "solar_radiation": "ERA5-Land",
    "spi": "CHIRPS", "spei": "CHIRPS + MODIS (Thornthwaite proxy)", "gdd": "MODIS LST", "lst": "MODIS LST",
}

def calculate_score(raw_values: Dict[str, Optional[float]], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """raw_values: dict with any subset of the 20 keys from
    comprehensive_score_service.DEFAULT_WEIGHTS (ndvi, evi, savi,
    msavi, ndre, ndmi, ndwi, ci_green, ci_rededge, vv, vh, vh_vv, rvi,
    rainfall, air_temp, solar_radiation, spi, spei, gdd, lst).

    Returns the same shape the rest of the app already expects:
    {"final_score": int(300-900), "grade": str, "components": {key: {...}}}
    """
    comp_result = compute_comprehensive_score(raw_values, weights=weights)

    if comp_result.get("score_0_100") is None:
        # No usable data at all — fail to the floor score rather than crash.
        logger.warning("calculate_score: no usable parameters, defaulting to floor score")
        return {
            "final_score": 300,
            "grade": DEFAULT_GRADE,
            "components": {},
        }

    final_score = comp_result["score_300_900"]
    grade = comp_result["grade"]

    components = {}
    for key, c in comp_result["components"].items():
        components[key] = {
            "raw_value": c["raw_value"],
            "sub_score": c["sub_score"],
            "weight": c["weight_pct"],
            "weighted_contribution": c.get("contribution"),
            "data_available": c["sub_score"] is not None,
            "unit": _UNITS.get(key, ""),
            "source": _SOURCES.get(key, ""),
            "label": PARAMETER_LABELS.get(key, key),
        }

    return {
        "final_score": final_score,
        "grade": grade,
        "components": components,
        "parameters_used": comp_result["parameters_used"],
        "parameters_total": comp_result["parameters_total"],
    }
