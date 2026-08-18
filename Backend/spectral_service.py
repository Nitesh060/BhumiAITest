"""
spectral_service.py
====================
Sentinel-2 multispectral crop-intelligence module for FarmScore.

This module uses real Sentinel-2 Surface Reflectance data. It does not
claim to use true hyperspectral imagery.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import ee

from earth_engine_service import (
    S2_MAX_CLOUD_PCT,
    _date_window,
    _get_region,
    _sentinel2_cloud_masked,
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


def _fetch_spectral_bands(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> Dict[str, Optional[float]]:
    """Mean NDVI / NDRE / GNDVI / NDMI / MSI over the recent growing-season
    Sentinel-2 composite, using the central Earth Engine cloud/shadow mask.
    """
    region, _region_mode = _get_region(lat, lng, polygon)
    start_date, end_date = _date_window()
    s2 = (
        _sentinel2_cloud_masked(lat, lng, polygon)
        .filterDate(start_date, end_date)
    )

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
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=10,
        maxPixels=1e9,
    ).getInfo() or {}

    return {
        band: (float(result[band]) if result.get(band) is not None else None)
        for band in bands
    }


def _chlorophyll_score(ndvi: float) -> float:
    return _clamp(ndvi * 100.0)


def _nitrogen_score(ndre: float) -> float:
    return _clamp((ndre + 0.1) / 0.6 * 100.0)


def _moisture_stress_score(msi: float) -> float:
    return _clamp((2.0 - msi) / 1.6 * 100.0)


def _stress_risk_score(
    chlorophyll_sc: float, moisture_sc: float, gndvi: float
) -> float:
    gndvi_sc = _clamp(gndvi / 0.8 * 100.0)
    spread = max(chlorophyll_sc, gndvi_sc, moisture_sc) - min(
        chlorophyll_sc, gndvi_sc, moisture_sc
    )
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


def calculate_spectral_intelligence(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> Dict[str, Any]:
    """Fetch real Sentinel-2 bands and compute the Spectral Health Score."""
    initialise_earth_engine()
    bands = _fetch_spectral_bands(lat, lng, polygon)

    def safe(key: str) -> tuple[float, bool]:
        value = bands.get(key)
        return (0.0, False) if value is None else (float(value), True)

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
        flags.append(
            "Possible nitrogen deficiency — red-edge reflectance below optimal canopy nitrogen range"
        )
    if moisture_sc < 40:
        flags.append(
            "Moisture stress detected — canopy water content lower than optimal (elevated SWIR/NIR ratio)"
        )
    if stress_sc < 40:
        flags.append(
            "Elevated disease/pest stress risk — vigor, chlorophyll and moisture signals are inconsistent"
        )
    if chlorophyll_sc < 40:
        flags.append("Low canopy vigor / chlorophyll — sparse or stressed vegetation cover")
    if not flags:
        flags.append("No significant stress signals detected in the current composite")

    start_date, end_date = _date_window()
    return {
        "spectral_score": spectral_score,
        "grade": grade,
        "method": (
            "Estimated from Sentinel-2 multispectral bands "
            "(NDVI/NDRE/GNDVI/NDMI/MSI); true hyperspectral imagery is not "
            "available for arbitrary coordinates."
        ),
        "data_window": {"start": start_date, "end": end_date},
        "cloud_mask": f"Sentinel-2 cloud probability <= {S2_MAX_CLOUD_PCT}% scene filter plus pixel-level cloud/shadow masking",
        "indices": {
            "chlorophyll": {
                "label": "Chlorophyll & Canopy Health",
                "raw_value": round(ndvi, 4),
                "index": "NDVI",
                "sub_score": round(chlorophyll_sc, 1),
                "weight": WEIGHTS["chlorophyll"],
                "status": _status_label(chlorophyll_sc),
                "data_available": ndvi_ok,
                "source": "Sentinel-2",
            },
            "nitrogen": {
                "label": "Nitrogen Status",
                "raw_value": round(ndre, 4),
                "index": "NDRE",
                "sub_score": round(nitrogen_sc, 1),
                "weight": WEIGHTS["nitrogen"],
                "status": _status_label(nitrogen_sc),
                "data_available": ndre_ok,
                "source": "Sentinel-2 (red-edge)",
            },
            "moisture_stress": {
                "label": "Moisture Stress",
                "raw_value": round(msi, 4),
                "index": "MSI",
                "sub_score": round(moisture_sc, 1),
                "weight": WEIGHTS["moisture_stress"],
                "status": _status_label(moisture_sc),
                "data_available": msi_ok,
                "source": "Sentinel-2 (SWIR/NIR)",
            },
            "stress_risk": {
                "label": "Disease / Stress Risk",
                "raw_value": round(gndvi, 4),
                "index": "GNDVI-consistency",
                "sub_score": round(stress_sc, 1),
                "weight": WEIGHTS["stress_risk"],
                "status": _status_label(stress_sc),
                "data_available": gndvi_ok and ndmi_ok,
                "source": "Sentinel-2",
            },
        },
        "flags": flags,
        "coordinates": {"lat": lat, "lng": lng},
    }
