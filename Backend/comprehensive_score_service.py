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
# 0-1000 scale, 5-tier bands — matches the reference SatSource-style
# report's exact category/interval mapping (Poor 400-625, Fair 626-725,
# Good 726-790, Very Good 791-870, Excellent 871-1000), not an
# independently-chosen scheme. Shared with seasonal_score_service.py's
# Base+Kharif+Rabi combiner via assign_grade() below, so the two never
# drift out of sync.
GRADE_BANDS = [(871, "Excellent"), (791, "Very Good"), (726, "Good"), (626, "Fair")]

def assign_grade(scaled_score: float) -> str:
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


# Score awarded at the two edges of a parameter's ideal band. Inside the
# band the score rises from this value to 100 at the band's centre, so only
# a genuinely optimal reading earns full marks.
#
# The previous curve returned a flat 100.0 anywhere inside the ideal band.
# Because those bands were also wide, an ordinary healthy Kharif paddy farm
# saturated 17 of the 20 parameters at exactly 100 and scored 99/100 — the
# index could not tell an average farm from an outstanding one.
PLATEAU_EDGE = 80.0


def _range_score(v: float, low: float, ideal_low: float, ideal_high: float, high: float) -> float:
    """Peaked suitability curve.

      0 at or beyond `low` / `high`
      ramping up to PLATEAU_EDGE at the ideal-band edges
      peaking at 100 in the centre of the ideal band
    """
    if v <= low or v >= high:
        return 0.0
    if v < ideal_low:
        return _clamp((v - low) / (ideal_low - low) * PLATEAU_EDGE)
    if v > ideal_high:
        return _clamp((high - v) / (high - ideal_high) * PLATEAU_EDGE)

    half_width = (ideal_high - ideal_low) / 2.0
    if half_width <= 0:
        return 100.0
    midpoint = (ideal_low + ideal_high) / 2.0
    offset = abs(v - midpoint) / half_width          # 0 at centre, 1 at edge
    return _clamp(100.0 - (100.0 - PLATEAU_EDGE) * offset ** 2)


# ---------------------------------------------------------------------------
# CALIBRATION TABLE — (low, ideal_low, ideal_high, high) per parameter.
#
# These are agronomic optima for irrigated/rainfed cereal systems, NOT values
# fitted to any reference report. They are the one place to tune the model:
# widen a band and that parameter becomes more forgiving, narrow it and it
# becomes stricter. `tools/calibrate_thresholds.py` fits them against a
# ground-truth sample.
#
# The previous bands were centred on merely-adequate readings (e.g. NDVI
# 0.45-0.80 scored full marks, when 0.45 is a thin canopy) which is what let
# an average farm reach the Excellent grade.
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "ndvi":            (0.10,   0.70,   0.85,   0.95),
    "evi":             (0.05,   0.45,   0.65,   0.85),
    "savi":            (0.05,   0.50,   0.70,   0.90),
    "msavi":           (0.05,   0.60,   0.80,   0.95),
    "ndre":            (0.05,   0.30,   0.45,   0.60),
    "ndmi":            (-0.30,  0.30,   0.50,   0.75),
    "ndwi":            (-0.50, -0.10,   0.20,   0.60),
    "ci_green":        (0.50,   4.00,   6.00,   9.00),
    "ci_rededge":      (0.30,   2.00,   3.50,   5.50),
    "vv":              (-25.0, -9.00,  -6.00,  -2.00),
    "vh":              (-30.0, -15.0,  -11.0,  -5.00),
    "vh_vv":           (0.05,   0.28,   0.45,   0.70),
    "rvi":             (0.10,   0.70,   1.10,   1.60),
    "rainfall":        (0.50,   4.00,   8.00,   16.0),
    "air_temp":        (8.00,   24.0,   30.0,   42.0),
    "solar_radiation": (6.00,   18.0,   24.0,   32.0),
    "gdd":             (400.0,  1600.0, 2600.0, 3600.0),
    "lst":             (8.00,   24.0,   30.0,   45.0),
}

# SPI/SPEI are anomalies: 0 is normal, and departure in EITHER direction is
# bad. The penalty per unit of |anomaly| was 20, so even a severe SPI of -2.0
# still scored 60/100. At 35 a moderate drought (-1.0) scores 65 and a severe
# one (-2.0) scores 30.
ANOMALY_PENALTY_PER_UNIT = 35.0


def _make_range_normalizer(key):
    low, ideal_low, ideal_high, high = THRESHOLDS[key]
    def _norm(v):
        return None if v is None else _range_score(v, low, ideal_low, ideal_high, high)
    return _norm


def _norm_anomaly(v):
    return None if v is None else _clamp(100.0 - abs(v) * ANOMALY_PENALTY_PER_UNIT)


_NORMALIZERS = {key: _make_range_normalizer(key) for key in THRESHOLDS}
_NORMALIZERS["spi"] = _norm_anomaly
_NORMALIZERS["spei"] = _norm_anomaly

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
    # GRADE_BANDS are defined on the 0-1000 scale, so grade there. The old
    # code graded `400 + score/100*600`, which lifted every farm by the
    # 400-point floor and compressed the rest into the top two bands: a
    # mediocre 55/100 became 730 and was labelled "Good".
    scaled = round(score / 100 * 1000)
    used = sum(1 for c in components.values() if c["sub_score"] is not None)
    confidence = "high" if used >= 15 else "moderate" if used >= 10 else "low"
    return {
        "score_0_100": score, "score_0_1000": scaled,
        # Legacy alias — same value, kept so older callers keep working.
        "score_400_1000": scaled,
        "grade": assign_grade(scaled),
        "confidence": confidence, "components": components, "parameters_used": used,
        "parameters_total": len(_NORMALIZERS), "parameter_groups": PARAMETER_GROUPS,
        "validation_status": "provisional — correlated parameter groups are explicitly tracked; empirical correlation/PCA calibration against ground-truth farms is still required",
        "method": "Weighted average of transparent 0-100 suitability sub-scores. Missing parameters are redistributed proportionally. LST and genuine 2m air temperature are separate signals; legacy MODIS LST duplicates in both fields are deduplicated.",
    }
