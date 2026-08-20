"""
spectral_indices.py
====================
Advanced optical and Sentinel-1 SAR parameters used by FarmScore.

All FarmScore optical/SAR parameters use the same central Earth Engine
region/date/cloud-mask helpers as the rest of the score pipeline. This avoids
the previous split where EVI/SAVI/MSAVI/chlorophyll/NDWI and SAR used a
hard-coded 2024 window while NDVI/NDMI/NDRE used the central dynamic window.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import ee

from earth_engine_service import (
    _date_window,
    _filter_season,
    _get_region,
    _reduce_mean,
    _sentinel2_cloud_masked,
    initialise_earth_engine,
)

logger = logging.getLogger(__name__)

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
S1_COLLECTION = "COPERNICUS/S1_GRD"


def _latest_s2_image(lat: float, lng: float, polygon: Optional[dict] = None):
    """Return the central, cloud/shadow-masked recent growing-season composite."""
    start, end = _date_window()
    coll = _filter_season(
        _sentinel2_cloud_masked(lat, lng, polygon).filterDate(start, end)
    )
    return coll.median()


def fetch_extended_indices(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """EVI, SAVI, MSAVI, BSI, CI_Green, CI_RedEdge and NDWI.

    Uses the same Sentinel-2 date window and pixel-level cloud/shadow mask as
    the core NDVI/NDMI/NDWI pipeline.
    """
    region = _get_region(lat, lng, polygon)
    img = _latest_s2_image(lat, lng, polygon)

    nir = img.select("B8").divide(10000)
    red = img.select("B4").divide(10000)
    green = img.select("B3").divide(10000)
    blue = img.select("B2").divide(10000)
    swir = img.select("B11").divide(10000)
    red_edge = img.select("B5").divide(10000)

    evi = nir.subtract(red).multiply(2.5).divide(
        nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)
    ).rename("EVI")

    savi = nir.subtract(red).divide(nir.add(red).add(0.5)).multiply(1.5).rename("SAVI")

    msavi = nir.multiply(2).add(1).subtract(
        nir.multiply(2).add(1).pow(2).subtract(nir.subtract(red).multiply(8)).sqrt()
    ).divide(2).rename("MSAVI")

    bsi = (red.add(swir)).subtract(nir.add(blue)).divide(
        (red.add(swir)).add(nir.add(blue))
    ).rename("BSI")

    ci_green = nir.divide(green).subtract(1).rename("CI_Green")
    ci_rededge = nir.divide(red_edge).subtract(1).rename("CI_RedEdge")
    ndwi = green.subtract(nir).divide(green.add(nir)).rename("NDWI")

    combined = ee.Image.cat([evi, savi, msavi, bsi, ci_green, ci_rededge, ndwi])
    result = combined.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9,
    ).getInfo() or {}

    def _r(key):
        v = result.get(key)
        return round(v, 4) if v is not None else None

    evi_val, savi_val, msavi_val, bsi_val = _r("EVI"), _r("SAVI"), _r("MSAVI"), _r("BSI")
    ci_green_val, ci_rededge_val, ndwi_val = _r("CI_Green"), _r("CI_RedEdge"), _r("NDWI")
    start, end = _date_window()

    return {
        "evi": evi_val,
        "evi_label": _evi_label(evi_val),
        "savi": savi_val,
        "savi_label": _savi_label(savi_val),
        "msavi": msavi_val,
        "bsi": bsi_val,
        "bsi_label": _bsi_label(bsi_val),
        "ci_green": ci_green_val,
        "ci_rededge": ci_rededge_val,
        "ndwi": ndwi_val,
        "source": "Sentinel-2 (central cloud/shadow-masked growing-season median)",
        "data_window": {"start": start, "end": end},
    }


def _evi_label(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if v < 0.2:
        return "Sparse/stressed vegetation"
    if v < 0.4:
        return "Moderate vegetation"
    return "Dense, healthy vegetation"


def _savi_label(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if v < 0.2:
        return "Low vegetation cover (soil-dominant signal)"
    if v < 0.4:
        return "Moderate cover"
    return "High vegetation cover"


def _bsi_label(v: Optional[float]) -> Optional[str]:
    if v is None:
        return None
    if v > 0.1:
        return "Significant bare soil exposure"
    if v > -0.1:
        return "Mixed soil/vegetation"
    return "Well-covered by vegetation"


def fetch_sar_moisture(
    lat: float,
    lng: float,
    polygon: Optional[dict] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> Dict[str, Any]:
    """Sentinel-1 VV/VH/RVI using the same recent growing-season window.

    The score uses a median composite rather than one arbitrary 2024 scene,
    reducing sensitivity to one acquisition date. VV/VH remain indicative
    backscatter proxies, not calibrated volumetric soil-moisture readings.
    """
    region = _get_region(lat, lng, polygon)
    if not start or not end:
        start, end = _date_window()

    coll = (
        ee.ImageCollection(S1_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.calendarRange(8, 10, "month"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(["VV", "VH"])
    )

    count = coll.size().getInfo()
    if count == 0:
        return {
            "available": False,
            "reason": "No Sentinel-1 scenes found for the configured growing-season window.",
            "source": "Sentinel-1 GRD",
            "data_window": {"start": start, "end": end},
        }

    composite = coll.median()
    stats = composite.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9
    ).getInfo() or {}

    vv, vh = stats.get("VV"), stats.get("VH")
    if vv is None or vh is None:
        return {
            "available": False,
            "reason": "Sentinel-1 VV/VH data unavailable for this location.",
            "source": "Sentinel-1 GRD",
            "data_window": {"start": start, "end": end},
        }

    vv, vh = float(vv), float(vh)
    vv_vh_diff_db = round(vv - vh, 2)
    vv_lin = 10 ** (vv / 10)
    vh_lin = 10 ** (vh / 10)
    vh_vv_ratio_linear = round(vh_lin / vv_lin, 4) if vv_lin else None
    rvi = round(4 * vh_lin / (vv_lin + vh_lin), 4) if (vv_lin + vh_lin) else None

    return {
        "available": True,
        "vv_db": round(vv, 2),
        "vh_db": round(vh, 2),
        "vv_vh_diff_db": vv_vh_diff_db,
        "vh_vv_ratio": vh_vv_ratio_linear,
        "rvi": rvi,
        "flood_signal": vv < -17,
        "note": "VV/VH backscatter is an indicative moisture/flood proxy, not calibrated volumetric soil moisture. RVI and VH/VV are computed from linear power converted from dB.",
        "source": "Sentinel-1 GRD (growing-season median)",
        "scenes_available_in_period": count,
        "data_window": {"start": start, "end": end},
    }
