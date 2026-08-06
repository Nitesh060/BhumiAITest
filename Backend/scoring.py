"""
Updated scoring.py (Prototype v2)

Changes:
- Improved NDMI normalization
- Rainfall benchmark updated to 6 mm/day
- Rest of API unchanged
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

WEIGHTS = {
    "groundwater": 25,
    "ndvi": 25,
    "ndmi": 20,
    "rainfall": 10,
    "temperature": 20,
}

GRADE_BANDS = [
    (781, "Excellent"),
    (661, "Good"),
    (541, "Average"),
    (421, "Fair"),
]

DEFAULT_GRADE = "Poor"

RAINFALL_BENCHMARK_MM = 6.0
TEMP_BENCHMARK_C = 30.0


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _rainfall_score(rainfall_mm_per_day: float) -> float:
    deviation = abs(RAINFALL_BENCHMARK_MM - rainfall_mm_per_day)
    score = 100.0 - 100.0 * deviation / RAINFALL_BENCHMARK_MM
    return _clamp(score)


def _temperature_score(temperature_c: float) -> float:
    deviation = abs(TEMP_BENCHMARK_C - temperature_c)
    score = 100.0 - 100.0 * deviation / TEMP_BENCHMARK_C
    return _clamp(score)


def _ndvi_score(ndvi: float) -> float:
    return _clamp(ndvi * 100.0)


def _ndmi_score(ndmi: float) -> float:
    # Improved normalization:
    # -1 -> 0, 0 -> 50, +1 -> 100
    return _clamp((ndmi + 1.0) * 50.0)


def _groundwater_score(groundwater_raw: float) -> float:
    return _clamp(groundwater_raw / 5.0)


def _assign_grade(final_score: int) -> str:
    for threshold, label in GRADE_BANDS:
        if final_score >= threshold:
            return label
    return DEFAULT_GRADE


def calculate_score(ndvi: Optional[float],
                    ndmi: Optional[float],
                    rainfall: Optional[float],
                    temperature: Optional[float],
                    groundwater: Optional[float]) -> Dict[str, Any]:

    def safe(v):
        return (0.0, False) if v is None else (float(v), True)

    ndvi_val, ndvi_ok = safe(ndvi)
    ndmi_val, ndmi_ok = safe(ndmi)
    rain_val, rain_ok = safe(rainfall)
    temp_val, temp_ok = safe(temperature)
    gw_val, gw_ok = safe(groundwater)

    gw_sc = _groundwater_score(gw_val)
    ndvi_sc = _ndvi_score(ndvi_val)
    ndmi_sc = _ndmi_score(ndmi_val)
    rain_sc = _rainfall_score(rain_val)
    temp_sc = _temperature_score(temp_val)

    weighted_avg = (
        WEIGHTS["groundwater"] * gw_sc +
        WEIGHTS["ndvi"] * ndvi_sc +
        WEIGHTS["ndmi"] * ndmi_sc +
        WEIGHTS["rainfall"] * rain_sc +
        WEIGHTS["temperature"] * temp_sc
    ) / 100.0

    final_score = round(300 + weighted_avg * 6)
    final_score = max(300, min(900, final_score))
    grade = _assign_grade(final_score)

    return {
        "final_score": final_score,
        "grade": grade,
        "components": {
            "groundwater": {"raw_value": gw_val, "sub_score": round(gw_sc,2), "weight":25,
                            "weighted_contribution": round(gw_sc*0.25,2), "data_available":gw_ok,
                            "unit":"kg/m²","source":"NASA GLDAS"},
            "ndvi": {"raw_value": round(ndvi_val,6), "sub_score": round(ndvi_sc,2), "weight":25,
                     "weighted_contribution": round(ndvi_sc*0.25,2), "data_available":ndvi_ok,
                     "unit":"","source":"Sentinel-2"},
            "ndmi": {"raw_value": round(ndmi_val,6), "sub_score": round(ndmi_sc,2), "weight":20,
                     "weighted_contribution": round(ndmi_sc*0.20,2), "data_available":ndmi_ok,
                     "unit":"","source":"Sentinel-2"},
            "rainfall": {"raw_value": round(rain_val,4), "sub_score": round(rain_sc,2), "weight":10,
                         "weighted_contribution": round(rain_sc*0.10,2), "data_available":rain_ok,
                         "unit":"mm/day","source":"CHIRPS"},
            "temperature": {"raw_value": round(temp_val,4), "sub_score": round(temp_sc,2), "weight":20,
                            "weighted_contribution": round(temp_sc*0.20,2), "data_available":temp_ok,
                            "unit":"°C","source":"MODIS LST"},
        },
    }
