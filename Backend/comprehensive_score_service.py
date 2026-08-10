"""
comprehensive_score_service.py
================================
A single weighted-average score across all 20 requested parameters
(Vegetation, Radar, Weather, Temperature) — separate from the main
FarmScore (which uses 5 core inputs: NDVI, NDMI, rainfall, temperature,
groundwater). This is a broader, more granular composite for users who
want every available signal folded into one number.

Every parameter is normalized to a 0-100 sub-score using a documented,
transparent formula (see _NORMALIZERS below) before being combined —
same "show your work" pattern as the rest of this app's scoring.

Default weights (category totals, then split evenly within category):
  Vegetation  40%  (9 params  -> ~4.44% each)
  Radar       20%  (4 params  -> 5% each)
  Weather     30%  (6 params  -> 5% each)
  Temperature 10%  (1 param   -> 10%)

Weights are fully overridable via the `weights` argument — the
defaults are a starting point, not a claimed-optimal configuration
(no ground-truth yield data exists yet to calibrate them against).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    # Vegetation — 40% total
    "ndvi": 4.44, "evi": 4.44, "savi": 4.44, "msavi": 4.44, "ndre": 4.44,
    "ndmi": 4.44, "ndwi": 4.44, "ci_green": 4.44, "ci_rededge": 4.44,
    # Radar — 20% total
    "vv": 5.0, "vh": 5.0, "vh_vv": 5.0, "rvi": 5.0,
    # Weather — 30% total
    "rainfall": 5.0, "air_temp": 5.0, "solar_radiation": 5.0,
    "spi": 5.0, "spei": 5.0, "gdd": 5.0,
    # Temperature — 10% total
    "lst": 10.0,
}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Normalizers — raw value -> 0-100 sub-score, one per parameter.
# Thresholds are documented literature-typical ranges for Indian field
# crops, not fitted to any specific dataset this app has.
# ---------------------------------------------------------------------------

def _norm_ndvi(v):       return None if v is None else _clamp(v / 0.90 * 100)
def _norm_evi(v):        return None if v is None else _clamp(v / 0.60 * 100)
def _norm_savi(v):       return None if v is None else _clamp(v / 0.70 * 100)
def _norm_msavi(v):      return None if v is None else _clamp(v / 0.90 * 100)
def _norm_ndre(v):       return None if v is None else _clamp(v / 0.40 * 100)
def _norm_ndmi(v):       return None if v is None else _clamp((v + 1.0) * 50.0)
def _norm_ndwi(v):       return None if v is None else _clamp((0.3 - v) / 0.6 * 100)  # more negative NDWI = more vegetated (less water) here
def _norm_ci_green(v):   return None if v is None else _clamp(v / 5.0 * 100)
def _norm_ci_rededge(v): return None if v is None else _clamp(v / 3.0 * 100)

def _norm_vv(v):         return None if v is None else _clamp((v + 20) / 15 * 100)   # -20dB->0, -5dB->100
def _norm_vh(v):         return None if v is None else _clamp((v + 25) / 15 * 100)   # -25dB->0, -10dB->100
def _norm_vh_vv(v):      return None if v is None else _clamp(v / 0.4 * 100)          # 0-0.4 typical
def _norm_rvi(v):        return None if v is None else _clamp(v * 100)                # already 0-1

def _norm_rainfall(v, benchmark=6.0):
    if v is None:
        return None
    deviation = abs(benchmark - v)
    return _clamp(100.0 - 100.0 * deviation / benchmark)

def _norm_air_temp(v, benchmark=30.0):
    if v is None:
        return None
    deviation = abs(benchmark - v)
    return _clamp(100.0 - 100.0 * deviation / benchmark)

def _norm_solar(v):      return None if v is None else _clamp(v / 22.0 * 100)   # ~22 MJ/m2/day = strong
def _norm_spi(v):        return None if v is None else _clamp(100 - abs(v) * 25)  # 0=normal=100, |SPI|>=4 -> 0
def _norm_spei(v):       return None if v is None else _clamp(100 - abs(v) * 25)
def _norm_gdd(v, target=1500):
    if v is None:
        return None
    return _clamp(v / target * 100)

def _norm_lst(v, benchmark=30.0):
    if v is None:
        return None
    deviation = abs(benchmark - v)
    return _clamp(100.0 - 100.0 * deviation / benchmark)


_NORMALIZERS = {
    "ndvi": _norm_ndvi, "evi": _norm_evi, "savi": _norm_savi, "msavi": _norm_msavi,
    "ndre": _norm_ndre, "ndmi": _norm_ndmi, "ndwi": _norm_ndwi,
    "ci_green": _norm_ci_green, "ci_rededge": _norm_ci_rededge,
    "vv": _norm_vv, "vh": _norm_vh, "vh_vv": _norm_vh_vv, "rvi": _norm_rvi,
    "rainfall": _norm_rainfall, "air_temp": _norm_air_temp, "solar_radiation": _norm_solar,
    "spi": _norm_spi, "spei": _norm_spei, "gdd": _norm_gdd, "lst": _norm_lst,
}

PARAMETER_LABELS = {
    "ndvi": "NDVI (Vegetation Health)", "evi": "EVI (Enhanced Vegetation Index)",
    "savi": "SAVI (Soil Adjusted Vegetation Index)", "msavi": "MSAVI (Modified SAVI)",
    "ndre": "NDRE (Nitrogen / Red Edge Health)", "ndmi": "NDMI (Moisture Index)",
    "ndwi": "NDWI (Water Index)", "ci_green": "CI_Green (Chlorophyll Index Green)",
    "ci_rededge": "CI_RedEdge (Chlorophyll Index Red Edge)",
    "vv": "VV (Radar Vertical-Vertical Backscatter)", "vh": "VH (Radar Vertical-Horizontal Backscatter)",
    "vh_vv": "VH/VV Ratio", "rvi": "RVI (Radar Vegetation Index)",
    "rainfall": "Rainfall", "air_temp": "Air Temperature (LST proxy)",
    "solar_radiation": "Solar Radiation", "spi": "SPI (Standardized Precipitation Index)",
    "spei": "SPEI (Thornthwaite proxy)", "gdd": "GDD (Growing Degree Days)",
    "lst": "LST (Land Surface Temperature)",
}


def compute_comprehensive_score(raw_values: Dict[str, Optional[float]],
                                 weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """raw_values: {"ndvi": 0.64, "evi": 0.45, ..., "lst": 29.1, ...} —
    any subset of the 20 parameter keys above. Missing/None values are
    excluded from the average and their weight is redistributed
    proportionally across the parameters that ARE available, so a
    missing SPEI doesn't silently zero out 5% of the score.
    """
    weights = weights or DEFAULT_WEIGHTS

    components = {}
    available_weight_sum = 0.0

    for key, raw in raw_values.items():
        if key not in _NORMALIZERS:
            continue
        normalizer = _NORMALIZERS[key]
        sub_score = normalizer(raw)
        weight = weights.get(key, 0)
        components[key] = {
            "label": PARAMETER_LABELS.get(key, key),
            "raw_value": raw,
            "sub_score": round(sub_score, 2) if sub_score is not None else None,
            "weight_pct": weight,
        }
        if sub_score is not None:
            available_weight_sum += weight

    if available_weight_sum == 0:
        return {"score": None, "reason": "No usable parameters were provided.", "components": components}

    weighted_sum = 0.0
    for key, c in components.items():
        if c["sub_score"] is not None:
            # redistribute weight proportionally among available params
            effective_weight = c["weight_pct"] / available_weight_sum
            contribution = effective_weight * c["sub_score"]
            c["effective_weight_pct"] = round(effective_weight * 100, 2)
            c["contribution"] = round(contribution, 2)
            weighted_sum += contribution

    final_score_0_100 = round(weighted_sum, 2)
    final_score_300_900 = round(300 + (final_score_0_100 / 100) * 600)  # match main FarmScore's 300-900 scale for consistency

    if final_score_0_100 >= 80:
        grade = "Excellent"
    elif final_score_0_100 >= 65:
        grade = "Good"
    elif final_score_0_100 >= 50:
        grade = "Average"
    elif final_score_0_100 >= 35:
        grade = "Fair"
    else:
        grade = "Poor"

    return {
        "score_0_100": final_score_0_100,
        "score_300_900": final_score_300_900,
        "grade": grade,
        "components": components,
        "parameters_used": len([c for c in components.values() if c["sub_score"] is not None]),
        "parameters_total": len(components),
        "method": "Weighted average of 0-100 normalized sub-scores per parameter. Missing parameters' weight is redistributed proportionally, not dropped silently.",
    }
