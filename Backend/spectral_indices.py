"""
spectral_indices.py
====================
Phase 2 — Advanced Satellite Intelligence.

Two families of new signals, both from real satellite data:

1. Additional optical vegetation indices (EVI, SAVI, BSI) — computed
   from Sentinel-2 the same way NDVI/NDMI/NDRE already are elsewhere
   in this app (see spectral_service.py, which already covers NDRE —
   not duplicated here).

2. Sentinel-1 SAR (SAR = Synthetic Aperture Radar) — a genuinely new
   data source for this app. SAR sees through cloud cover, which
   matters a lot in India's monsoon season when optical satellites
   (Sentinel-2) can go weeks without a clear image. Used here for a
   soil-moisture-and-flood signal that optical data can't reliably
   give during exactly the season farmers need it most.

Index reference:
    EVI  = 2.5 * (NIR-Red) / (NIR + 6*Red - 7.5*Blue + 1)     bands B8,B4,B2
    SAVI = ((NIR-Red)/(NIR+Red+L)) * (1+L), L=0.5              bands B8,B4
    BSI  = ((Red+SWIR)-(NIR+Blue)) / ((Red+SWIR)+(NIR+Blue))   bands B4,B11,B8,B2
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean, _buffered_region

logger = logging.getLogger(__name__)

S2_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
S1_COLLECTION = "COPERNICUS/S1_GRD"


def _latest_s2_image(region, start="2024-01-01", end="2024-12-31", max_cloud=20):
    coll = (
        ee.ImageCollection(S2_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )
    return coll.median()


def fetch_extended_indices(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """EVI, SAVI, BSI for one farm — extends the NDVI/NDMI/NDRE already
    computed elsewhere with three more standard remote-sensing indices.
    """
    region = _get_region(lat, lng, polygon)
    img = _latest_s2_image(region)

    nir = img.select("B8").divide(10000)
    red = img.select("B4").divide(10000)
    blue = img.select("B2").divide(10000)
    swir = img.select("B11").divide(10000)

    evi = nir.subtract(red).multiply(2.5).divide(
        nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)
    ).rename("EVI")

    L = 0.5
    savi = nir.subtract(red).divide(nir.add(red).add(L)).multiply(1 + L).rename("SAVI")

    bsi = (red.add(swir)).subtract(nir.add(blue)).divide(
        (red.add(swir)).add(nir.add(blue))
    ).rename("BSI")

    combined = ee.Image.cat([evi, savi, bsi])
    result = combined.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9,
    ).getInfo()

    def _r(key):
        v = result.get(key)
        return round(v, 4) if v is not None else None

    evi_val, savi_val, bsi_val = _r("EVI"), _r("SAVI"), _r("BSI")

    return {
        "evi": evi_val,
        "evi_label": _evi_label(evi_val),
        "savi": savi_val,
        "savi_label": _savi_label(savi_val),
        "bsi": bsi_val,
        "bsi_label": _bsi_label(bsi_val),
        "source": "Sentinel-2 (median composite, <20% cloud)",
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


# ---------------------------------------------------------------------------
# Sentinel-1 SAR — soil moisture / flood signal, cloud-penetrating
# ---------------------------------------------------------------------------

def fetch_sar_moisture(lat: float, lng: float, polygon: Optional[dict] = None,
                        start: str = "2024-01-01", end: str = "2024-12-31") -> Dict[str, Any]:
    """VV/VH backscatter from Sentinel-1 — works through cloud cover,
    which matters most exactly when farmers need it (monsoon season,
    when optical Sentinel-2 often can't get a clear shot for weeks).

    Rough interpretation (well-established in SAR literature, not this
    app's own invention):
      - Very low VV (< -17 dB) → standing water / flooding
      - VV/VH backscatter trends correlate with soil moisture and crop
        biomass, but this is an INDICATIVE proxy, not a calibrated
        volumetric soil-moisture measurement (that needs field
        calibration this app doesn't have).
    """
    region = _get_region(lat, lng, polygon)

    coll = (
        ee.ImageCollection(S1_COLLECTION)
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .select(["VV", "VH"])
    )

    count = coll.size().getInfo()
    if count == 0:
        return {"available": False, "reason": "No Sentinel-1 scenes found for this date range/location.", "source": "Sentinel-1 GRD"}

    latest = coll.sort("system:time_start", False).first()
    stats = latest.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9).getInfo()

    vv, vh = stats.get("VV"), stats.get("VH")
    if vv is None:
        return {"available": False, "reason": "Sentinel-1 data unavailable for this location.", "source": "Sentinel-1 GRD"}

    flood_signal = vv < -17
    vv_vh_ratio = round(vv - vh, 2) if vh is not None else None  # dB difference

    return {
        "available": True,
        "vv_db": round(vv, 2),
        "vh_db": round(vh, 2) if vh is not None else None,
        "vv_vh_diff_db": vv_vh_ratio,
        "flood_signal": flood_signal,
        "note": "VV/VH backscatter — indicative moisture/flood proxy, not a calibrated volumetric soil-moisture reading.",
        "source": "Sentinel-1 GRD (most recent scene, cloud-penetrating radar)",
        "scenes_available_in_period": count,
    }
