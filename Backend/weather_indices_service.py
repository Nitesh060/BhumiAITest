"""
weather_indices_service.py
============================
Solar Radiation, SPI, GDD, and a simplified SPEI proxy — the weather
parameters needed for the comprehensive weighted-average score that
aren't already covered by satellite_data's basic rainfall/temperature
or weather_soil_terrain_service's historical weather.

HONESTY NOTE on SPEI: the real formula needs potential
evapotranspiration (PET) from Penman-Monteith, which needs wind speed
and humidity data this app doesn't have wired up. This module uses
the simpler Thornthwaite PET method (temperature-only) as a documented
approximation — labeled "SPEI (Thornthwaite proxy)" everywhere it
appears, never presented as the full standard.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, List, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean

logger = logging.getLogger(__name__)

GDD_BASE_TEMP_C = 10.0  # common base temperature for many field crops


def fetch_solar_radiation(lat: float, lng: float, polygon: Optional[dict] = None,
                           start: str = "2024-06-01", end: str = "2024-10-31") -> Dict[str, Any]:
    """Average daily surface solar radiation (MJ/m2/day) over the given
    window, from ERA5-Land's daily-aggregated downward solar radiation.
    """
    region = _get_region(lat, lng, polygon)
    try:
        coll = (
            ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR")
            .filterDate(start, end)
            .filterBounds(region)
            .select("surface_solar_radiation_downwards_sum")
        )
        val_j = _reduce_mean(coll.mean(), region, scale=10000)
        if val_j is None:
            return {"available": False, "reason": "No ERA5-Land solar radiation data for this window/location."}
        val_mj = val_j / 1_000_000  # J/m2 -> MJ/m2
        return {
            "available": True,
            "avg_daily_solar_radiation_mj_m2": round(val_mj, 2),
            "source": "ECMWF ERA5-Land Daily Aggregate",
        }
    except Exception:
        logger.exception("Solar radiation fetch failed")
        return {"available": False, "reason": "Solar radiation fetch failed."}


def fetch_spi(lat: float, lng: float, polygon: Optional[dict] = None,
              current_year: Optional[int] = None, history_years: int = 10) -> Dict[str, Any]:
    """Standardized Precipitation Index — z-score of the current
    season's rainfall against the same window's rainfall in the
    previous `history_years` years. SPI ~0 = normal, negative = drier
    than usual (drought signal), positive = wetter than usual.
    """
    if current_year is None:
        current_year = datetime.utcnow().year

    region = _get_region(lat, lng, polygon)
    yearly_totals: List[float] = []

    for year in range(current_year - history_years, current_year + 1):
        coll = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(f"{year}-06-01", f"{year}-10-31")
            .filterBounds(region)
        )
        val = _reduce_mean(coll.sum(), region, scale=5000)
        if val is not None:
            yearly_totals.append(val)

    if len(yearly_totals) < 4:
        return {"available": False, "reason": "Insufficient rainfall history to compute a meaningful SPI (need several years)."}

    current = yearly_totals[-1]
    history = yearly_totals[:-1]
    mean = sum(history) / len(history)
    variance = sum((x - mean) ** 2 for x in history) / len(history)
    stddev = math.sqrt(variance)

    if stddev == 0:
        return {"available": False, "reason": "Zero variance in rainfall history — cannot compute SPI."}

    spi = round((current - mean) / stddev, 2)

    if spi <= -2:
        category = "Extreme drought"
    elif spi <= -1.5:
        category = "Severe drought"
    elif spi <= -1:
        category = "Moderate drought"
    elif spi < 1:
        category = "Near normal"
    elif spi < 1.5:
        category = "Moderately wet"
    else:
        category = "Very wet"

    return {
        "available": True,
        "spi": spi,
        "category": category,
        "current_season_rainfall_mm": round(current, 1),
        "historical_mean_mm": round(mean, 1),
        "historical_stddev_mm": round(stddev, 1),
        "years_used": len(history),
        "source": "CHIRPS (Jun-Oct window, current vs prior years)",
    }


def fetch_gdd(lat: float, lng: float, polygon: Optional[dict] = None,
              start: str = "2024-06-01", end: str = "2024-10-31",
              base_temp_c: float = GDD_BASE_TEMP_C) -> Dict[str, Any]:
    """Growing Degree Days — cumulative (daily_mean_temp - base_temp)
    for days where that's positive, over the given window. Uses MODIS
    LST as the daily temperature source (consistent with the rest of
    this app's temperature figures).
    """
    region = _get_region(lat, lng, polygon)
    try:
        coll = (
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterDate(start, end)
            .filterBounds(region)
            .select("LST_Day_1km")
            .map(lambda img: img.multiply(0.02).subtract(273.15).subtract(base_temp_c).max(0).rename("GDD_daily"))
        )
        total_img = coll.sum()
        val = _reduce_mean(total_img, region, scale=1000)
        if val is None:
            return {"available": False, "reason": "No temperature data available for GDD computation."}

        return {
            "available": True,
            "gdd": round(val, 1),
            "base_temp_c": base_temp_c,
            "window": f"{start} to {end}",
            "note": "Computed from MODIS daytime LST, not true air temperature — an approximation.",
            "source": "MODIS LST",
        }
    except Exception:
        logger.exception("GDD fetch failed")
        return {"available": False, "reason": "GDD computation failed."}


def fetch_spei_proxy(lat: float, lng: float, polygon: Optional[dict] = None,
                      current_year: Optional[int] = None) -> Dict[str, Any]:
    """A SIMPLIFIED SPEI using Thornthwaite potential evapotranspiration
    (temperature-only — no wind/humidity data available). This is a
    documented approximation, not the full standard SPEI method.
    """
    if current_year is None:
        current_year = datetime.utcnow().year

    region = _get_region(lat, lng, polygon)

    try:
        rain_coll = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(f"{current_year}-06-01", f"{current_year}-10-31")
            .filterBounds(region)
        )
        rainfall_total = _reduce_mean(rain_coll.sum(), region, scale=5000)

        temp_coll = (
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterDate(f"{current_year}-06-01", f"{current_year}-10-31")
            .filterBounds(region)
            .select("LST_Day_1km")
            .map(lambda img: img.multiply(0.02).subtract(273.15))
        )
        avg_temp = _reduce_mean(temp_coll.mean(), region, scale=1000)

        if rainfall_total is None or avg_temp is None or avg_temp <= 0:
            return {"available": False, "reason": "Insufficient rainfall/temperature data for SPEI proxy."}

        # Thornthwaite monthly PET (simplified, applied to the 5-month
        # Jun-Oct window as one block): PET = 16 * (10*T/I)^a
        # I (heat index) and a depend on T; using the common simplified
        # single-period form here rather than true monthly iteration.
        heat_index = (avg_temp / 5) ** 1.514 if avg_temp > 0 else 0
        a = 6.75e-7 * heat_index**3 - 7.71e-5 * heat_index**2 + 1.792e-2 * heat_index + 0.49239
        pet_monthly_mm = 16 * ((10 * avg_temp / heat_index) ** a) if heat_index > 0 else 0
        pet_season_mm = pet_monthly_mm * 5  # 5-month Jun-Oct window

        water_balance = rainfall_total - pet_season_mm
        # crude standardization against a generic reference range since
        # this proxy doesn't have a multi-year water-balance history
        spei_proxy = round(max(-3, min(3, water_balance / 300)), 2)

        if spei_proxy <= -1.5:
            category = "Dry stress (proxy)"
        elif spei_proxy < 1.5:
            category = "Near normal (proxy)"
        else:
            category = "Excess moisture (proxy)"

        return {
            "available": True,
            "spei_proxy": spei_proxy,
            "category": category,
            "rainfall_mm": round(rainfall_total, 1),
            "estimated_pet_mm": round(pet_season_mm, 1),
            "method": "Thornthwaite PET (temperature-only) vs rainfall, standardized against a generic range — a simplified proxy, NOT the full standard SPEI (which needs wind/humidity data this app doesn't have).",
            "source": "CHIRPS + MODIS LST",
        }
    except Exception:
        logger.exception("SPEI proxy fetch failed")
        return {"available": False, "reason": "SPEI proxy computation failed."}
