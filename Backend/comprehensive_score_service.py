"""Transparent 0-100 composite FarmScore model.

This is a suitability/condition index, not a validated yield or credit-risk
model. Thresholds and weights remain provisional until ground-truth farm
outcomes are available.
"""
from __future__ import annotations
from typing import Any, Dict, Optional

DEFAULT_WEIGHTS = {
    "ndvi": 5.0, "evi": 5.0, "savi": 5.0, "msavi": 5.0, "ndre": 5.0,
    "ndmi": 5.0, "ndwi": 5.0, "ci_green": 5.0, "ci_rededge": 5.0,
    "vv": 5.0, "vh": 5.0, "vh_vv": 5.0, "rvi": 5.0,
    "rainfall": 5.0, "air_temp": 5.0, "solar_radiation": 5.0,
    "spi": 5.0, "spei": 5.0, "gdd": 5.0, "lst": 10.0,
}

PARAMETER_GROUPS = {
    "vegetation_health": ["ndvi", "evi", "savi", "msavi"],
    "crop_health_red_edge": ["ndre", "ci_green", "ci_rededge"],
    "moisture_water": ["ndmi", "ndwi"],
    "radar": ["vv", "vh", "vh_vv", "rvi"],
    "precipitation_water_balance": ["rainfall", "spi", "spei"],
    "temperature_heat": ["air_temp", "lst", "gdd"],
    "solar": ["solar_radiation"],
}

DEFAULT_GRADE = "Poor"
GRADE_BANDS = [(781, "Excellent"), (661, "Good"), (541, "Average"), (421, "Fair")]

def _assign_grade(scaled_score: float) -> str:
    for threshold, label in GRADE_BANDS:
        if scaled_score >= threshold:
            return label
    return DEFAULT_GRADE

PARAMETER_LABELS = {
    "ndvi": "NDVI (Vegetation Health)", "evi": "EVI (Enhanced Vegetation Index)",
    "savi": "SAVI (Soil Adjusted Vegetation Index)", "msavi": "MSAVI (Modified SAVI)",
    "ndre": "NDRE (Red Edge / Crop Health)", "ndmi": "NDMI (Vegetation Moisture)",
    "ndwi": "NDWI (Surface Water / Vegetation Water Signal)",
    "ci_green": "CI_Green (Chlorophyll Index)", "ci_rededge": "CI_RedEdge (Chlorophyll Index)",
    "vv": "VV (Radar Backscatter)", "vh": "VH (Cross-polarized Backscatter)",
    "vh_vv": "VH/VV Ratio", "rvi": "RVI (Radar Vegetation Index)",
    "rainfall": "Rainfall", "air_temp": "Air Temperature", "solar_radiation": "Solar Radiation",
    "spi": "SPI (Precipitation Anomaly)", "spei": "SPEI (Water Balance Proxy)",
    "gdd": "GDD (Growing Degree Days)", "lst": "LST (Land Surface Temperature)",
}

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))

def _range_score(v: float, low: float, ideal_low: float, ideal_high: float, high: float) -> float:
    if v <= low or v >= high:
        return 0.0
    if ideal_low <= v <= ideal_high:
        return 100.0
    if v < ideal_low:
        return _clamp((v - low) / (ideal_low - low) * 100.0)
    return _clamp((high - v) / (high - ideal_high) * 100.0)

def _norm_ndvi(v): return None if v is None else _range_score(v, 0.10, 0.45, 0.80, 0.95)
def _norm_evi(v): return None if v is None else _range_score(v, 0.02, 0.25, 0.55, 0.75)
def _norm_savi(v): return None if v is None else _range_score(v, 0.02, 0.25, 0.60, 0.85)
def _norm_msavi(v): return None if v is None else _range_score(v, 0.02, 0.35, 0.75, 0.95)
def _norm_ndre(v): return None if v is None else _range_score(v, 0.02, 0.15, 0.35, 0.50)
def _norm_ndmi(v): return None if v is None else _range_score(v, -0.60, 0.10, 0.50, 0.80)
def _norm_ndwi(v): return None if v is None else _range_score(v, -0.60, -0.30, 0.15, 0.70)
def _norm_ci_green(v): return None if v is None else _range_score(v, 0.0, 1.0, 4.0, 8.0)
def _norm_ci_rededge(v): return None if v is None else _range_score(v, 0.0, 0.7, 2.5, 5.0)
def _norm_vv(v): return None if v is None else _range_score(v, -30.0, -18.0, -7.0, 0.0)
def _norm_vh(v): return None if v is None else _range_score(v, -35.0, -23.0, -8.0, 0.0)
def _norm_vh_vv(v): return None if v is None else _range_score(v, 0.02, 0.10, 0.35, 0.70)
def _norm_rvi(v): return None if v is None else _range_score(v, 0.0, 0.20, 0.70, 1.50)
def _norm_rainfall(v): return None if v is None else _range_score(v, 0.0, 2.0, 6.0, 15.0)
def _norm_air_temp(v): return None if v is None else _range_score(v, 5.0, 18.0, 32.0, 45.0)
def _norm_solar(v): return None if v is None else _range_score(v, 4.0, 12.0, 24.0, 35.0)
def _norm_spi(v): return None if v is None else _clamp(100.0 - abs(v) * 20.0)
def _norm_spei(v): return None if v is None else _clamp(100.0 - abs(v) * 20.0)
def _norm_gdd(v): return None if v is None else _range_score(v, 200.0, 900.0, 2200.0, 3500.0)
def _norm_lst(v): return None if v is None else _range_score(v, 5.0, 18.0, 32.0, 45.0)

_NORMALIZERS = {"ndvi":_norm_ndvi,"evi":_norm_evi,"savi":_norm_savi,"msavi":_norm_msavi,"ndre":_norm_ndre,"ndmi":_norm_ndmi,"ndwi":_norm_ndwi,"ci_green":_norm_ci_green,"ci_rededge":_norm_ci_rededge,"vv":_norm_vv,"vh":_norm_vh,"vh_vv":_norm_vh_vv,"rvi":_norm_rvi,"rainfall":_norm_rainfall,"air_temp":_norm_air_temp,"solar_radiation":_norm_solar,"spi":_norm_spi,"spei":_norm_spei,"gdd":_norm_gdd,"lst":_norm_lst}

def compute_comprehensive_score(raw_values: Dict[str, Optional[float]], weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    # Make a copy so DEFAULT_WEIGHTS is never modified globally.
    weights = dict(weights or DEFAULT_WEIGHTS)
    raw_values = dict(raw_values)

    # The API historically passed the same MODIS LST value into both
    # temperature fields. Do not count that same signal twice. Once a real
    # ERA5-Land 2m air-temperature value is supplied, it will normally differ
    # from LST and both signals can contribute independently.
    air_temp = raw_values.get("air_temp")
    lst = raw_values.get("lst")
    if air_temp is not None and lst is not None:
        try:
            if abs(float(air_temp) - float(lst)) < 0.01:
                # Remove the duplicate air-temperature signal.
                raw_values["air_temp"] = None

                # Also remove its weight so it cannot contribute to
                # available_weight_sum or the final score.
                weights["air_temp"] = 0.0
        except (TypeError, ValueError):
            pass

    components = {}
    available_weight_sum = 0.0
    for key, raw in raw_values.items():
        if key not in _NORMALIZERS:
            continue
        try:
            numeric = None if raw is None else float(raw)
        except (TypeError, ValueError):
            numeric = None
        sub_score = _NORMALIZERS[key](numeric)
        weight = max(0.0, float(weights.get(key, 0.0)))
        components[key] = {"label": PARAMETER_LABELS.get(key, key), "raw_value": numeric, "sub_score": round(sub_score, 2) if sub_score is not None else None, "weight_pct": weight}
        if sub_score is not None and weight > 0:
            available_weight_sum += weight
    if available_weight_sum <= 0:
        return {"score_0_100": None, "reason": "No usable parameters were provided.", "components": components}
    weighted_sum = 0.0
    for c in components.values():
        if c["sub_score"] is not None and c["weight_pct"] > 0:
            effective_weight = c["weight_pct"] / available_weight_sum
            c["effective_weight_pct"] = round(effective_weight * 100, 2)
            c["contribution"] = round(effective_weight * c["sub_score"], 2)
            weighted_sum += effective_weight * c["sub_score"]
    score = round(weighted_sum, 2)
    scaled = round(300 + (score / 100) * 600)
    used = sum(1 for c in components.values() if c["sub_score"] is not None)
    confidence = "high" if used >= 15 else "moderate" if used >= 10 else "low"
    return {
        "score_0_100": score, "score_300_900": scaled, "grade": _assign_grade(scaled),
        "confidence": confidence, "components": components, "parameters_used": used,
        "parameters_total": len(_NORMALIZERS), "parameter_groups": PARAMETER_GROUPS,
        "validation_status": "provisional — correlated parameter groups are explicitly tracked; empirical correlation/PCA calibration against ground-truth farms is still required",
        "method": "Weighted average of transparent 0-100 suitability sub-scores. Missing parameters are redistributed proportionally. LST and genuine 2m air temperature are separate signals; legacy duplicate temperature inputs are de-duplicated automatically. Thresholds are provisional and require ground-truth calibration before credit decisions.",
    }
