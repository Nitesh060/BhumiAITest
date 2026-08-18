"""Weather indices used by the FarmScore comprehensive score.

SPEI is explicitly a Thornthwaite temperature-only proxy, not the full
standard SPEI. Climate windows use completed seasons to avoid current-data
publication lag producing false nulls.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean

logger = logging.getLogger(__name__)
GDD_BASE_TEMP_C = 10.0


def _weather_region(lat: float, lng: float, polygon: Optional[dict]) -> ee.Geometry:
    """Use a local buffer for coarse climate grids (5-11 km pixels).

    The parcel polygon is still used by high-resolution satellite metrics,
    but ERA5/CHIRPS climate values represent a grid cell and are more robust
    when reduced over a small surrounding area.
    """
    return _get_region(lat, lng, polygon).buffer(5000)


def _completed_season_window(year: int) -> tuple[str, str]:
    return f"{year}-06-01", f"{year}-11-01"


def _latest_completed_year() -> int:
    return datetime.utcnow().year - 1


def fetch_solar_radiation(lat: float, lng: float, polygon: Optional[dict] = None,
                           start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    """Average daily surface solar radiation in MJ/m²/day from ERA5-Land."""
    region = _weather_region(lat, lng, polygon)
    year = _latest_completed_year()
    start, end = (start, end) if start and end else _completed_season_window(year)
    try:
        coll = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
                .filterDate(start, end).filterBounds(region)
                .select("surface_solar_radiation_downwards_sum"))
        if coll.size().getInfo() == 0:
            return {"available": False, "reason": "No ERA5-Land solar radiation scenes in the completed climate window."}
        # The *_sum band is daily accumulated J/m². Average the daily
        # accumulated field, then convert J/m² to MJ/m²/day.
        val_j = _reduce_mean(coll.mean(), region, scale=11132)
        if val_j is None:
            # Fallback to the net downward solar sum if the primary band
            # cannot be reduced for this grid cell.
            fallback = (ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
                        .filterDate(start, end).filterBounds(region)
                        .select("surface_net_solar_radiation_sum"))
            if fallback.size().getInfo():
                val_j = _reduce_mean(fallback.mean(), region, scale=11132)
        if val_j is None:
            return {"available": False, "reason": "ERA5-Land solar radiation reduction returned no value."}
        return {"available": True, "avg_daily_solar_radiation_mj_m2": round(max(0.0, val_j) / 1_000_000, 2),
                "window": f"{start} to {end}", "source": "ECMWF ERA5-Land Daily Aggregate"}
    except Exception as exc:
        logger.exception("Solar radiation fetch failed")
        return {"available": False, "reason": f"Solar radiation fetch failed: {type(exc).__name__}"}


def fetch_spi(lat: float, lng: float, polygon: Optional[dict] = None,
              current_year: Optional[int] = None, history_years: int = 10) -> Dict[str, Any]:
    """Seasonal precipitation anomaly z-score using completed years."""
    region = _weather_region(lat, lng, polygon)
    current_year = current_year or _latest_completed_year()
    if current_year >= datetime.utcnow().year:
        current_year = datetime.utcnow().year - 1
    start_year = current_year - history_years
    years_list = ee.List.sequence(start_year, current_year)
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")

    def _season_total(y):
        y = ee.Number(y)
        start = ee.Date.fromYMD(y, 6, 1)
        end = ee.Date.fromYMD(y, 11, 1)
        total_img = chirps.filterDate(start, end).filterBounds(region).sum()
        val = total_img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region,
                                      scale=5566, maxPixels=1e9, bestEffort=True).get("precipitation")
        return ee.Feature(None, {"year": y, "rainfall_mm": val})

    try:
        raw = ee.FeatureCollection(years_list.map(_season_total)).getInfo()
    except Exception as exc:
        logger.exception("Batched SPI fetch failed")
        return {"available": False, "reason": f"Rainfall history fetch failed: {type(exc).__name__}"}

    features = sorted(raw.get("features", []), key=lambda f: f["properties"]["year"])
    pairs = [(f["properties"]["year"], f["properties"].get("rainfall_mm")) for f in features]
    valid = [(y, v) for y, v in pairs if v is not None]
    current_pair = next(((y, v) for y, v in valid if y == current_year), None)
    if current_pair is None:
        return {"available": False, "reason": f"No completed CHIRPS rainfall data for {current_year}."}
    history = [v for y, v in valid if y < current_year]
    if len(history) < 4:
        return {"available": False, "reason": "Insufficient completed rainfall history for SPI."}
    current = current_pair[1]
    mean = sum(history) / len(history)
    stddev = math.sqrt(sum((x - mean) ** 2 for x in history) / len(history))
    if stddev == 0:
        return {"available": False, "reason": "Zero variance in rainfall history — cannot compute SPI."}
    spi = round((current - mean) / stddev, 2)
    if spi <= -2: category = "Extreme drought"
    elif spi <= -1.5: category = "Severe drought"
    elif spi <= -1: category = "Moderate drought"
    elif spi < 1: category = "Near normal"
    elif spi < 1.5: category = "Moderately wet"
    else: category = "Very wet"
    return {"available": True, "spi": spi, "category": category,
            "current_season_rainfall_mm": round(current, 1),
            "historical_mean_mm": round(mean, 1), "historical_stddev_mm": round(stddev, 1),
            "years_used": len(history), "season_year": current_year,
            "source": "CHIRPS (Jun-Oct completed-season window)"}


def fetch_gdd(lat: float, lng: float, polygon: Optional[dict] = None,
              start: Optional[str] = None, end: Optional[str] = None,
              base_temp_c: float = GDD_BASE_TEMP_C) -> Dict[str, Any]:
    """Growing Degree Days from MODIS daytime LST, as an approximation."""
    region = _weather_region(lat, lng, polygon)
    year = _latest_completed_year()
    start, end = (start, end) if start and end else _completed_season_window(year)
    try:
        coll = (ee.ImageCollection("MODIS/061/MOD11A1").filterDate(start, end).filterBounds(region)
                .select("LST_Day_1km")
                .map(lambda img: img.multiply(0.02).subtract(273.15).subtract(base_temp_c).max(0).rename("GDD_daily")))
        if coll.size().getInfo() == 0:
            return {"available": False, "reason": "No MODIS temperature data available for GDD."}
        val = _reduce_mean(coll.sum(), region, scale=1000)
        if val is None:
            return {"available": False, "reason": "GDD reduction returned no value."}
        return {"available": True, "gdd": round(val, 1), "base_temp_c": base_temp_c,
                "window": f"{start} to {end}",
                "note": "Computed from MODIS daytime LST, not true air temperature — an approximation.",
                "source": "MODIS LST"}
    except Exception as exc:
        logger.exception("GDD fetch failed")
        return {"available": False, "reason": f"GDD computation failed: {type(exc).__name__}"}


def fetch_spei_proxy(lat: float, lng: float, polygon: Optional[dict] = None,
                      current_year: Optional[int] = None) -> Dict[str, Any]:
    """Simplified Thornthwaite PET water-balance proxy."""
    region = _weather_region(lat, lng, polygon)
    current_year = current_year or _latest_completed_year()
    if current_year >= datetime.utcnow().year:
        current_year = datetime.utcnow().year - 1
    try:
        rain_coll = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                     .filterDate(f"{current_year}-06-01", f"{current_year}-11-01")
                     .filterBounds(region))
        rainfall_total = _reduce_mean(rain_coll.sum(), region, scale=5566)
        temp_coll = (ee.ImageCollection("MODIS/061/MOD11A1")
                     .filterDate(f"{current_year}-06-01", f"{current_year}-11-01")
                     .filterBounds(region).select("LST_Day_1km")
                     .map(lambda img: img.multiply(0.02).subtract(273.15)))
        avg_temp = _reduce_mean(temp_coll.mean(), region, scale=1000)
        if rainfall_total is None or avg_temp is None or avg_temp <= 0:
            return {"available": False, "reason": f"Insufficient completed-season data for SPEI proxy ({current_year})."}
        heat_index = (avg_temp / 5) ** 1.514
        a = 6.75e-7 * heat_index**3 - 7.71e-5 * heat_index**2 + 1.792e-2 * heat_index + 0.49239
        pet_monthly_mm = 16 * ((10 * avg_temp / heat_index) ** a) if heat_index > 0 else 0
        pet_season_mm = pet_monthly_mm * 5
        water_balance = rainfall_total - pet_season_mm
        spei_proxy = round(max(-3, min(3, water_balance / 300)), 2)
        if spei_proxy <= -1.5: category = "Dry stress (proxy)"
        elif spei_proxy < 1.5: category = "Near normal (proxy)"
        else: category = "Excess moisture (proxy)"
        return {"available": True, "spei_proxy": spei_proxy, "category": category,
                "rainfall_mm": round(rainfall_total, 1), "estimated_pet_mm": round(pet_season_mm, 1),
                "season_year": current_year,
                "method": "Thornthwaite PET (temperature-only) vs rainfall; simplified proxy, NOT the full standard SPEI.",
                "source": "CHIRPS + MODIS LST"}
    except Exception as exc:
        logger.exception("SPEI proxy fetch failed")
        return {"available": False, "reason": f"SPEI proxy computation failed: {type(exc).__name__}"}
