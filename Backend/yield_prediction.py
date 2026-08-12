"""
yield_prediction.py
====================
A transparent, formula-based yield estimate — NOT a trained ML model
(that needs 2000+ labeled farm-seasons of ground-truth harvest data,
which this app doesn't have yet).

Method
------
NDVI-proportional scaling against India's published national-average
yield per crop:

    estimated_yield = national_avg_yield × (farm_NDVI / reference_NDVI)

`reference_NDVI` is the NDVI threshold this app's own crop
recommendation engine (crop_recommendation.py) already treats as
"healthy" for that crop — so a farm scoring at that NDVI gets ~100% of
the national average yield; healthier farms score above it, stressed
farms score below it. The ratio is clipped to 0.3–1.3x to avoid wild
extrapolation from a single vegetation index.

This is a documented starting point, not a substitute for a properly
trained model — the moment you have real yield data (from field
officers, harvest records), replace this with a regression/ML model
trained on YOUR farms and compare its accuracy against this proxy's.

References (approximate, for the "reference_ndvi" and "avg_yield_kg_ha"
values below): Ministry of Agriculture & Farmers Welfare (India)
published state/national average yield statistics; NDVI thresholds
match crop_recommendation.py so the two stay consistent with each
other.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# NDVI thresholds match crop_recommendation.py's own "healthy" cutoffs,
# so a farm just clearing that bar is treated as ~average yield for
# that crop — not an independently chosen number. reference_evi/
# reference_ndre match the same file's EVI/NDRE bonus thresholds.
CROP_YIELD_REFERENCE = {
    "Rice": {
        "reference_ndvi": 0.60, "reference_evi": 0.40, "reference_ndre": 0.25,
        "avg_yield_kg_ha": 2650,
        "source": "India national average paddy yield (indicative, MoA&FW-published range)",
    },
    "Wheat": {
        "reference_ndvi": 0.45, "reference_evi": 0.30, "reference_ndre": 0.20,
        "avg_yield_kg_ha": 3400,
        "source": "India national average wheat yield (indicative, MoA&FW-published range)",
    },
    "Maize": {
        "reference_ndvi": 0.50, "reference_evi": 0.35, "reference_ndre": 0.22,
        "avg_yield_kg_ha": 3000,
        "source": "India national average maize yield (indicative, MoA&FW-published range)",
    },
    "Groundnut": {
        "reference_ndvi": 0.40, "reference_evi": 0.28, "reference_ndre": 0.18,
        "avg_yield_kg_ha": 1300,
        "source": "India national average groundnut yield (indicative, MoA&FW-published range)",
    },
}

MIN_RATIO = 0.3   # floor: even a stressed field rarely yields <30% of average
MAX_RATIO = 1.3   # ceiling: a single NDVI reading shouldn't imply >130% of average


def estimate_yield(crop: str, ndvi: Optional[float], area_ha: Optional[float] = None,
                    evi: Optional[float] = None, ndre: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Returns a yield estimate dict for `crop`, or None if the crop
    isn't in the reference table or NDVI is missing. evi/ndre are
    optional — when given (already fetched by compute_farmscore's
    comprehensive model, no extra satellite call), they're blended
    with NDVI into a more robust health ratio than NDVI alone, which
    is known to saturate in dense canopy.
    """
    ref = CROP_YIELD_REFERENCE.get(crop)
    if not ref or ndvi is None:
        return None

    ratios = [ndvi / ref["reference_ndvi"]]
    indices_used = ["ndvi"]
    if evi is not None and ref.get("reference_evi"):
        ratios.append(evi / ref["reference_evi"])
        indices_used.append("evi")
    if ndre is not None and ref.get("reference_ndre"):
        ratios.append(ndre / ref["reference_ndre"])
        indices_used.append("ndre")

    blended_ratio = sum(ratios) / len(ratios)
    ratio_clipped = max(MIN_RATIO, min(blended_ratio, MAX_RATIO))
    yield_kg_ha = round(ref["avg_yield_kg_ha"] * ratio_clipped)

    result: Dict[str, Any] = {
        "crop": crop,
        "estimated_yield_kg_per_ha": yield_kg_ha,
        "reference_avg_yield_kg_per_ha": ref["avg_yield_kg_ha"],
        "ndvi_used": round(ndvi, 4),
        "reference_ndvi": ref["reference_ndvi"],
        "indices_blended": indices_used,
        "ratio_clipped": blended_ratio != ratio_clipped,
        "method": f"Blended-index scaling ({'+'.join(indices_used).upper()}) against national average yield — a formula-based proxy, not a trained ML model.",
        "source": ref["source"],
    }

    if area_ha is not None and area_ha > 0:
        total_kg = yield_kg_ha * area_ha
        result["area_ha"] = round(area_ha, 3)
        result["estimated_total_yield_kg"] = round(total_kg)
        result["estimated_total_yield_quintal"] = round(total_kg / 100, 1)
    else:
        result["note"] = "No farm boundary provided — only per-hectare yield estimated, not total tonnage."

    return result


def compute_polygon_area_ha(polygon: Optional[dict]) -> Optional[float]:
    """Computes area in hectares from a GeoJSON-style polygon using
    Earth Engine's geodesic area calculation. Returns None if no
    polygon was drawn (e.g. WhatsApp location-pin-only farms).
    """
    if not polygon:
        return None
    try:
        import ee
        from earth_engine_service import extract_polygon_coordinates
        coords = extract_polygon_coordinates(polygon)
        if not coords:
            return None
        geom = ee.Geometry.Polygon([coords])
        area_m2 = geom.area(maxError=1).getInfo()
        return area_m2 / 10000.0
    except Exception:
        logger.exception("Polygon area computation failed")
        return None
