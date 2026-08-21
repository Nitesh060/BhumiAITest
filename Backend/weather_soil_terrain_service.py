"""
weather_soil_terrain_service.py
=================================
Phase 4 — Weather, Soil & Terrain Intelligence.

Elevation/Slope and Drought were already built (see
enrichment_service.fetch_topography / fetch_drought_instances) — not
duplicated here.

New in this module:
  - Historical Weather: multi-year rainfall + temperature trend
  - Soil Health: pH, Organic Carbon, Nitrogen (OpenLandMap). Phosphorus
    and Potassium are NOT included — no reliable global satellite
    dataset exists for them; that's lab-soil-test territory (India's
    Soil Health Card scheme), not something Earth Engine can give you.
  - Soil Moisture: SMAP volumetric soil moisture — a genuine upgrade
    over the GLDAS groundwater proxy already used in the main
    FarmScore (that one estimates deeper terrestrial water storage;
    SMAP measures near-surface (0-5cm) moisture directly).
  - Flood Risk: combines JRC seasonal-water history + local slope +
    the Sentinel-1 flood signal already built in Phase 2.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import ee

from earth_engine_service import (
    _get_region, _reduce_mean, _buffered_region,
    _scaled_region, _reduce_mean_with_retry,
    CHIRPS_SCALE_M, MODIS_LST_SCALE_M,
)

logger = logging.getLogger(__name__)

# NASA SMAP SPL4SMGP's native grid is ~9km (commonly rounded to 10km for
# reduceRegion scale purposes, matching the scale value this module
# already used before this fix) — no shared constant for it exists
# alongside CHIRPS/MODIS/ERA5/GLDAS's in earth_engine_service.py, so
# it's defined locally here.
SMAP_SCALE_M = 10000


# ---------------------------------------------------------------------------
# Historical Weather — multi-year rainfall + temperature trend
# ---------------------------------------------------------------------------

def fetch_historical_weather(lat: float, lng: float, polygon: Optional[dict] = None,
                              start_year: int = 2015, end_year: Optional[int] = None) -> Dict[str, Any]:
    """Root cause of this always returning empty/null years: reduceRegion
    ran against the bare `_get_region()` geometry — a ~30m point buffer,
    or a modest farm polygon — regardless of CHIRPS's (~5.5km) or MODIS
    LST's (~1km) actual pixel size. A weighted `ee.Reducer.mean()` only
    counts a pixel if the query geometry covers at least ~0.4% of that
    pixel's area (see earth_engine_service._min_scale_buffer_m's
    docstring); a 30m buffer covers a negligible fraction of either,
    so this reduced to None on essentially every year, every request.
    Switched to `_scaled_region`/`_reduce_mean_with_retry` — the same
    fix already proven for these exact datasets elsewhere in this app.
    """
    if end_year is None:
        end_year = datetime.utcnow().year

    chirps_region = _scaled_region(lat, lng, polygon, CHIRPS_SCALE_M)
    modis_region = _scaled_region(lat, lng, polygon, MODIS_LST_SCALE_M)
    yearly: List[Dict[str, Any]] = []

    for year in range(start_year, end_year + 1):
        rain_coll = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(f"{year}-01-01", f"{year}-12-31")
            .filterBounds(chirps_region)
        )
        rain_total = rain_coll.sum()
        rain_val = _reduce_mean_with_retry(rain_total, lat, lng, polygon, CHIRPS_SCALE_M)

        temp_coll = (
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterDate(f"{year}-01-01", f"{year}-12-31")
            .filterBounds(modis_region)
            .select("LST_Day_1km")
            .map(lambda img: img.multiply(0.02).subtract(273.15).rename("LST_C"))
        )
        temp_val = _reduce_mean_with_retry(temp_coll.mean(), lat, lng, polygon, MODIS_LST_SCALE_M)

        yearly.append({
            "year": year,
            "total_rainfall_mm": round(rain_val, 1) if rain_val is not None else None,
            "avg_temperature_c": round(temp_val, 2) if temp_val is not None else None,
        })

    valid_rain = [y["total_rainfall_mm"] for y in yearly if y["total_rainfall_mm"] is not None]
    avg_rainfall = round(sum(valid_rain) / len(valid_rain), 1) if valid_rain else None

    return {
        "yearly": yearly,
        "long_term_avg_rainfall_mm": avg_rainfall,
        "start_year": start_year,
        "end_year": end_year,
        "source": "CHIRPS (rainfall) + MODIS LST (temperature)",
    }


# ---------------------------------------------------------------------------
# Soil Health — pH, Organic Carbon, Nitrogen (NOT P/K — see module docstring)
# ---------------------------------------------------------------------------

def fetch_soil_health(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region = _get_region(lat, lng, polygon)

    ph_img = ee.Image("OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02").select("b0")
    oc_img = ee.Image("OpenLandMap/SOL/SOL_ORGANIC-CARBON_USDA-6A1C_M/v02").select("b0")
    n_img = ee.Image("OpenLandMap/SOL/SOL_NITROGEN_USDA-6A1C_M/v02").select("b0")

    combined = ee.Image.cat([
        ph_img.rename("ph"), oc_img.rename("oc"), n_img.rename("n"),
    ])
    result = combined.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=250, maxPixels=1e9).getInfo()

    ph_raw = result.get("ph")
    oc_raw = result.get("oc")
    n_raw = result.get("n")

    # OpenLandMap encodes pH *10 and organic carbon/nitrogen in g/kg *10 — undo the scaling
    ph = round(ph_raw / 10, 2) if ph_raw is not None else None
    oc_g_per_kg = round(oc_raw / 10, 2) if oc_raw is not None else None
    n_g_per_kg = round(n_raw / 10, 2) if n_raw is not None else None

    return {
        "ph": ph,
        "ph_label": _ph_label(ph),
        "organic_carbon_g_per_kg": oc_g_per_kg,
        "organic_carbon_label": _oc_label(oc_g_per_kg),
        "nitrogen_g_per_kg": n_g_per_kg,
        "phosphorus": None,
        "potassium": None,
        "npk_note": "Phosphorus and Potassium are NOT available — no reliable global satellite dataset exists for them. "
                    "These require a physical soil test (e.g. India's Soil Health Card scheme).",
        "source": "OpenLandMap (0 cm depth, 250m resolution)",
    }


def _ph_label(ph: Optional[float]) -> Optional[str]:
    if ph is None:
        return None
    if ph < 5.5:
        return "Acidic"
    if ph < 7.5:
        return "Neutral"
    return "Alkaline"


def _oc_label(oc: Optional[float]) -> Optional[str]:
    if oc is None:
        return None
    if oc < 5:
        return "Low organic matter"
    if oc < 15:
        return "Moderate organic matter"
    return "High organic matter"


# ---------------------------------------------------------------------------
# Soil Moisture — SMAP, true near-surface volumetric moisture
# ---------------------------------------------------------------------------

def fetch_soil_moisture(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """SMAP gives actual volumetric soil moisture (m3/m3, 0-5cm depth) —
    unlike the GLDAS terrestrial-water-storage proxy already used
    elsewhere in this app (which is deeper and less direct).
    """
    # Same unscaled-region bug as fetch_historical_weather above: SMAP's
    # native pixel is ~9-10km, and a bare `_get_region()` (~30m point
    # buffer) covers a negligible fraction of one — `_reduce_mean`
    # reduced to None on essentially every request. Fixed the same way.
    region = _scaled_region(lat, lng, polygon, SMAP_SCALE_M)

    try:
        coll = (
            ee.ImageCollection("NASA/SMAP/SPL4SMGP/007")
            .filterBounds(region)
            .filterDate(_recent_window())
            .select("sm_surface")
        )
        count = coll.size().getInfo()
        if count == 0:
            return {"available": False, "reason": "No recent SMAP scenes for this location.", "source": "NASA SMAP"}

        val = _reduce_mean_with_retry(coll.mean(), lat, lng, polygon, SMAP_SCALE_M)
        if val is None:
            return {"available": False, "reason": "SMAP data unavailable for this location.", "source": "NASA SMAP"}

        return {
            "available": True,
            "surface_soil_moisture_m3_m3": round(val, 4),
            "label": _moisture_label(val),
            "source": "NASA SMAP (0-5cm volumetric soil moisture, ~9km resolution)",
            "note": "Coarse resolution (~9km) — represents the general area, not necessarily this specific field.",
        }
    except Exception:
        logger.exception("SMAP soil moisture fetch failed")
        return {"available": False, "reason": "SMAP fetch failed.", "source": "NASA SMAP"}


def _recent_window():
    from datetime import timedelta
    end = datetime.utcnow()
    start = end - timedelta(days=10)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _moisture_label(v: float) -> str:
    if v < 0.15:
        return "Dry"
    if v < 0.30:
        return "Moderate"
    return "Wet / saturated"


# ---------------------------------------------------------------------------
# Flood Risk — combines JRC seasonal water history + slope + (optionally)
# the Sentinel-1 flood signal from Phase 2
# ---------------------------------------------------------------------------

def fetch_flood_risk(lat: float, lng: float, polygon: Optional[dict] = None,
                      slope_degrees: Optional[float] = None, sar_flood_signal: Optional[bool] = None) -> Dict[str, Any]:
    region = _buffered_region(lat, lng, 500) if not polygon else _get_region(lat, lng, polygon)

    try:
        gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("seasonality")
        seasonality = _reduce_mean(gsw, region, scale=30)
    except Exception:
        logger.exception("Flood risk JRC fetch failed")
        seasonality = None

    risk_score = 0
    factors = []

    if seasonality is not None and seasonality > 1:
        risk_score += 40
        factors.append(f"Seasonal water history nearby ({round(seasonality,1)} months/year on average)")

    if slope_degrees is not None and slope_degrees < 2:
        risk_score += 30
        factors.append("Flat terrain (slope < 2°) — poor natural drainage")

    if sar_flood_signal:
        risk_score += 30
        factors.append("Recent Sentinel-1 radar flood signal detected")

    if risk_score >= 60:
        level = "High"
    elif risk_score >= 30:
        level = "Moderate"
    else:
        level = "Low"

    return {
        "risk_level": level,
        "risk_score": risk_score,
        "factors": factors,
        "seasonality_months_per_year": round(seasonality, 2) if seasonality is not None else None,
        "note": "Composite heuristic (water history + terrain + radar signal), not a hydrological flood model.",
        "source": "JRC Global Surface Water + SRTM slope + Sentinel-1 (where available)",
    }
