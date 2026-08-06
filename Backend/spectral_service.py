"""
spectral_service.py
====================
Hyperspectral-style crop-intelligence module for FarmScore.

HONESTY NOTE (read before touching thresholds)
------------------------------------------------
True hyperspectral sensors (EnMAP, PRISMA, AVIRIS, ...) are not available
as an on-demand, any-point-on-earth API the way Sentinel-2 is. This
module does NOT claim to read hyperspectral imagery. Instead it computes
a set of well-established MULTISPECTRAL vegetation indices from real
Sentinel-2 Surface Reflectance bands (via the same Earth Engine account
already used by earth_engine_service.py) that are the standard proxies
the remote-sensing literature uses for exactly the traits hyperspectral
sensors are prized for:

    Index   Bands used         Proxy for
    ------  -----------------  --------------------------------------
    NDVI    B8 (NIR), B4 (Red) Canopy vigor / chlorophyll content
    NDRE    B8 (NIR), B5 (RE)  Canopy nitrogen status (red-edge is far
                                more nitrogen-sensitive than red/NIR)
    GNDVI   B8 (NIR), B3 (Grn) Chlorophyll concentration (cross-check)
    NDMI    B8 (NIR), B11(SWIR) Canopy/leaf water content
    MSI     B11(SWIR), B8(NIR) Moisture Stress Index (inverse of NDMI,
                                more sensitive at high biomass)

These combine into a 0-100 "Spectral Health Score" and four sub-scores
(chlorophyll, nitrogen, moisture stress, disease/stress risk). When
Gemini is configured, an LLM turns the numbers into plain-language
irrigation / fertilization / crop-management guidance grounded ONLY in
the numbers computed below. When Gemini is unavailable, a deterministic
rule-based fallback keeps the module fully functional without an API
key — see gemini_service.generate_spectral_insight().
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import ee

from earth_engine_service import (
    START_DATE,
    END_DATE,
    S2_MAX_CLOUD_PCT,
    _get_region,
    _filter_growing_season,
    _reduce_mean,
    initialise_earth_engine,
)

logger = logging.getLogger(__name__)

WEIGHTS = {
    "chlorophyll": 30,
    "nitrogen": 25,
    "moisture_stress": 25,
    "stress_risk": 20,
}

GRADE_BANDS = [
    (85, "Excellent"),
    (70, "Good"),
    (50, "Moderate"),
    (30, "Fair"),
]
DEFAULT_GRADE = "Poor"


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _assign_grade(score: int) -> str:
    for threshold, label in GRADE_BANDS:
        if score >= threshold:
            return label
    return DEFAULT_GRADE


# ---------------------------------------------------------------------------
# Earth Engine fetch — one composite image, one reduceRegion call
# ---------------------------------------------------------------------------

def _fetch_spectral_bands(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Optional[float]]:
    """Mean NDVI / NDRE / GNDVI / NDMI / MSI over the growing-season
    Sentinel-2 composite, in a single reduceRegion call."""
    region = _get_region(lat, lng, polygon)
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT))
    )
    s2 = _filter_growing_season(s2)

    def compute(img: ee.Image) -> ee.Image:
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndre = img.normalizedDifference(["B8", "B5"]).rename("NDRE")
        gndvi = img.normalizedDifference(["B8", "B3"]).rename("GNDVI")
        ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
        msi = img.select("B11").divide(img.select("B8")).rename("MSI")
        return img.addBands([ndvi, ndre, gndvi, ndmi, msi])

    bands = ["NDVI", "NDRE", "GNDVI", "NDMI", "MSI"]
    composite = s2.map(compute).select(bands).mean()

    result = composite.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9
    ).getInfo()

    return {b: (float(result[b]) if result and result.get(b) is not None else None) for b in bands}


# ---------------------------------------------------------------------------
# Sub-score formulas — transparent, documented linear mappings
# ---------------------------------------------------------------------------

def _chlorophyll_score(ndvi: float) -> float:
    # 0 -> 0, 1 -> 100 (healthy dense canopy is typically NDVI 0.6-0.9)
    return _clamp(ndvi * 100.0)


def _nitrogen_score(ndre: float) -> float:
    # Healthy, well-fertilized canopy: NDRE ~ 0.35-0.55. Stressed/low-N: <0.2
    return _clamp((ndre + 0.1) / 0.6 * 100.0)


def _moisture_stress_score(msi: float) -> float:
    # MSI ~0.4 = well watered, MSI ~2.0 = severe stress -> inverted score
    return _clamp((2.0 - msi) / 1.6 * 100.0)


def _stress_risk_score(chlorophyll_sc: float, moisture_sc: float, gndvi: float) -> float:
    # Disease/pest stress often shows as a MISMATCH between vigor signals
    # (e.g. canopy still "green" per NDVI but chlorophyll concentration
    # per GNDVI and moisture status disagree) rather than a single low
    # index. We score the consistency between the three signals.
    gndvi_sc = _clamp(gndvi / 0.8 * 100.0)
    spread = max(chlorophyll_sc, gndvi_sc, moisture_sc) - min(chlorophyll_sc, gndvi_sc, moisture_sc)
    return _clamp(100.0 - spread)


def _status_label(pct: float) -> str:
    if pct >= 80:
        return "Excellent"
    if pct >= 60:
        return "Good"
    if pct >= 40:
        return "Moderate"
    if pct >= 20:
        return "Low"
    return "Poor"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_spectral_intelligence(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> Dict[str, Any]:
    """Fetch real Sentinel-2 bands and compute the Spectral Health Score.

    Returns a dict with per-index breakdown (raw value, sub-score, weight,
    status, source) plus a composite 0-100 score, grade, and rule-based
    flags. AI recommendations are attached separately by the caller
    (gemini_service.generate_spectral_insight) so this function has no
    external-API dependency and always succeeds if Earth Engine responds.
    """
    initialise_earth_engine()
    bands = _fetch_spectral_bands(lat, lng, polygon)

    def safe(key: str) -> tuple[float, bool]:
        v = bands.get(key)
        return (0.0, False) if v is None else (float(v), True)

    ndvi, ndvi_ok = safe("NDVI")
    ndre, ndre_ok = safe("NDRE")
    gndvi, gndvi_ok = safe("GNDVI")
    ndmi, ndmi_ok = safe("NDMI")
    msi, msi_ok = safe("MSI")

    chlorophyll_sc = _chlorophyll_score(ndvi)
    nitrogen_sc = _nitrogen_score(ndre)
    moisture_sc = _moisture_stress_score(msi)
    stress_sc = _stress_risk_score(chlorophyll_sc, moisture_sc, gndvi)

    weighted = (
        WEIGHTS["chlorophyll"] * chlorophyll_sc
        + WEIGHTS["nitrogen"] * nitrogen_sc
        + WEIGHTS["moisture_stress"] * moisture_sc
        + WEIGHTS["stress_risk"] * stress_sc
    ) / 100.0

    spectral_score = int(round(_clamp(weighted)))
    grade = _assign_grade(spectral_score)

    flags = []
    if nitrogen_sc < 40:
        flags.append("Possible nitrogen deficiency — red-edge reflectance below optimal canopy nitrogen range")
    if moisture_sc < 40:
        flags.append("Moisture stress detected — canopy water content lower than optimal (elevated SWIR/NIR ratio)")
    if stress_sc < 40:
        flags.append("Elevated disease/pest stress risk — vigor, chlorophyll and moisture signals are inconsistent")
    if chlorophyll_sc < 40:
        flags.append("Low canopy vigor / chlorophyll — sparse or stressed vegetation cover")
    if not flags:
        flags.append("No significant stress signals detected in the current composite")

    return {
        "spectral_score": spectral_score,
        "grade": grade,
        "method": "Estimated from Sentinel-2 multispectral bands (NDVI/NDRE/GNDVI/NDMI/MSI) — "
                  "true hyperspectral imagery is not available for arbitrary coordinates.",
        "indices": {
            "chlorophyll": {
                "label": "Chlorophyll & Canopy Health", "raw_value": round(ndvi, 4), "index": "NDVI",
                "sub_score": round(chlorophyll_sc, 1), "weight": WEIGHTS["chlorophyll"],
                "status": _status_label(chlorophyll_sc), "data_available": ndvi_ok, "source": "Sentinel-2",
            },
            "nitrogen": {
                "label": "Nitrogen Status", "raw_value": round(ndre, 4), "index": "NDRE",
                "sub_score": round(nitrogen_sc, 1), "weight": WEIGHTS["nitrogen"],
                "status": _status_label(nitrogen_sc), "data_available": ndre_ok, "source": "Sentinel-2 (red-edge)",
            },
            "moisture_stress": {
                "label": "Moisture Stress", "raw_value": round(msi, 4), "index": "MSI",
                "sub_score": round(moisture_sc, 1), "weight": WEIGHTS["moisture_stress"],
                "status": _status_label(moisture_sc), "data_available": msi_ok, "source": "Sentinel-2 (SWIR/NIR)",
            },
            "stress_risk": {
                "label": "Disease / Stress Risk", "raw_value": round(gndvi, 4), "index": "GNDVI-consistency",
                "sub_score": round(stress_sc, 1), "weight": WEIGHTS["stress_risk"],
                "status": _status_label(stress_sc), "data_available": gndvi_ok and ndmi_ok, "source": "Sentinel-2",
            },
        },
        "flags": flags,
        "coordinates": {"lat": lat, "lng": lng},
    }
