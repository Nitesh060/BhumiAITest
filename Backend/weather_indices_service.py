"""Weather indices used by the FarmScore comprehensive score."""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, Optional
import ee
from earth_engine_service import (
    _reduce_mean_with_retry,
    _scaled_region,
    _getinfo_with_backoff,
    CHIRPS_SCALE_M,
    MODIS_LST_SCALE_M,
    ERA5_LAND_SCALE_M,
)

# How many extra years back fetch_spi will search, beyond the minimum
# number of history years it needs, before giving up. A couple of
# sporadic per-year CHIRPS gaps shouldn't be able to sink the whole SPI
# computation when a few more years of history are just as valid — but
# this fans out to individual Earth Engine calls, each with its own
# retry/backoff (see earth_engine_service._getinfo_with_backoff), so it
# doubles as SPI_TIME_BUDGET_S's backstop rather than the primary limit:
# under a real, non-transient outage every one of those calls can burn
# its full retry budget, and 8 sequential worst-case calls adds up to
# minutes, not seconds (this happened in production — a single /calculate
# request took 171s largely inside this loop). Keep this modest; let the
# time budget below be what actually bounds worst-case latency.
SPI_HISTORY_SEARCH_SLACK = 2

# Hard wall-clock cap on how long fetch_spi will keep searching for
# history once the current season's value is in hand. If Earth Engine
# is having a sustained (not transient) bad time, retrying 8 more years
# each with their own multi-attempt backoff can turn one parameter into
# a multi-minute request — better to stop, report what was found (or
# that nothing was), and let the rest of the FarmScore computation
# proceed than to make the whole request pay for it.
#
# Root cause of SPI's "always no data" (found after Rainfall/solar/GDD/
# SPEI were already fixed): unlike those, which only need ONE season's
# worth of monthly CHIRPS/ERA5 calls (5-10 total) to succeed, SPI needs
# `history_years` (4) separate valid years PLUS the current season —
# each year requiring its own 5 monthly CHIRPS calls. At 20s, that
# budget assumed every one of those ~25-30 Earth Engine round trips
# would land in under ~1s with zero retries; in practice a handful of
# months needing _reduce_mean_with_retry's buffer-widening retries was
# enough to burn the whole budget before 4 valid years were ever found
# — every single request, at every location, hence "consistently".
# _season_rainfall_mm below now fetches a year's 5 months concurrently
# (same fix as Rainfall's monthly fetch), cutting each year's cost to
# roughly its slowest single month instead of the sum of all 5 — but
# the budget is also raised here, generously, since the gunicorn
# worker timeout (Procfile: --timeout 300) leaves ample room and a
# still-too-tight budget was the original failure mode.
SPI_TIME_BUDGET_S = 60.0

logger = logging.getLogger(__name__)
GDD_BASE_TEMP_C = 10.0
CHIRPS_COLLECTION = "UCSB-CHG/CHIRPS/DAILY"

def _weather_region(lat: float, lng: float, polygon: Optional[dict], scale_m: float = ERA5_LAND_SCALE_M) -> ee.Geometry:
    """Region used only for `.filterBounds()` calls (finding overlapping
    tiles) — NOT for the reduceRegion() averaging step itself, which needs a
    region sized to the dataset's own pixel scale (see `_scaled_region` in
    earth_engine_service.py) or ee.Reducer.mean() silently returns null for
    these coarse global grids (CHIRPS ~5.5km, ERA5-Land ~11km, MODIS ~1km).
    """
    return _scaled_region(lat, lng, polygon, scale_m)

def _completed_season_window(year: int) -> tuple[str, str]:
    return f"{year}-06-01", f"{year}-11-01"

def _month_windows(year: int, months: tuple = (6, 7, 8, 9, 10)) -> list:
    """The 5 Jun-Oct month windows for a season year, as (start, end)
    date-string pairs — the same month boundaries the rainfall-chart
    fetch (earth_engine_service._fetch_rainfall_monthly) already uses.

    Every function below used to run ONE reduceRegion() over the full
    ~153-day season window in a single shot. In production this was
    consistently failing under this service's resource constraints —
    confirmed first for rainfall's score value (which a single 5-month
    CHIRPS aggregation kept failing on, while the PDF's 5 separate
    ~30-day monthly aggregations reliably succeeded), then found to be
    the same underlying issue for every parameter here that used the
    identical wide-window pattern: solar radiation, SPI, SPEI, GDD —
    every one of them was showing "no data" at every location. Breaking
    each season into these 5 smaller chunks and combining the results
    (sum-of-sums for additive quantities like rainfall/GDD, mean-of-
    means for rate quantities like solar radiation/temperature) fixes
    all of them by the same construction that fixed rainfall.
    """
    out = []
    for m in months:
        start = f"{year}-{m:02d}-01"
        end = f"{year}-{m + 1:02d}-01" if m < 12 else f"{year + 1}-01-01"
        out.append((start, end))
    return out

def _latest_completed_year() -> int:
    return datetime.utcnow().year - 1

def fetch_solar_radiation(lat: float, lng: float, polygon: Optional[dict] = None, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    try:
        filter_region=_weather_region(lat,lng,polygon,ERA5_LAND_SCALE_M); year=_latest_completed_year()
        # Try the latest completed season first; if that whole year comes
        # back with no usable months, fall back one year earlier instead
        # of "no data" outright.
        candidate_years = [] if start and end else [year, year - 1]
        last_reason = "ERA5-Land solar radiation reduction returned no value."
        for cy in candidate_years:
            monthly_vals = []
            for w_start, w_end in _month_windows(cy):
                coll=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(w_start,w_end).filterBounds(filter_region).select("surface_solar_radiation_downwards_sum")
                v=_reduce_mean_with_retry(coll.mean(),lat,lng,polygon,ERA5_LAND_SCALE_M)
                if v is not None:
                    monthly_vals.append(v)
            if monthly_vals:
                val = sum(monthly_vals) / len(monthly_vals)
                w_start, w_end = _completed_season_window(cy)
                return {"available":True,"avg_daily_solar_radiation_mj_m2":round(max(0.0,val)/1_000_000,2),"window":f"{w_start} to {w_end}","months_used":len(monthly_vals),"source":"ECMWF ERA5-Land Daily Aggregate"}
            last_reason = f"No month in {cy}'s Jun-Oct window returned a usable ERA5-Land value."
        if start and end:
            coll=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start,end).filterBounds(filter_region).select("surface_solar_radiation_downwards_sum")
            val=_reduce_mean_with_retry(coll.mean(),lat,lng,polygon,ERA5_LAND_SCALE_M)
            if val is not None:
                return {"available":True,"avg_daily_solar_radiation_mj_m2":round(max(0.0,val)/1_000_000,2),"window":f"{start} to {end}","source":"ECMWF ERA5-Land Daily Aggregate"}
            last_reason = f"ERA5-Land solar radiation reduction returned no value for {start} to {end}."
        return {"available":False,"reason":last_reason}
    except Exception as exc:
        # Region/window setup used to sit OUTSIDE this try block, so an
        # exception there (e.g. a transient Earth Engine geometry/auth
        # error) would escape this function entirely uncaught, losing the
        # "reason" text — the caller (_safe_score_fetch in app.py) only saw
        # a bare exception with no way to attach it to this component.
        # Everything now runs inside the try so a reason is always returned.
        logger.exception("Solar radiation fetch failed")
        return {"available":False,"reason":f"Solar radiation fetch failed: {type(exc).__name__}: {exc}"}

def _season_rainfall_mm(lat: float, lng: float, polygon: Optional[dict], year: int) -> Optional[float]:
    """Total Jun-Oct seasonal rainfall (mm) for one year.

    Fetches the 5 monthly CHIRPS sums CONCURRENTLY (previously
    sequential) — see the SPI_TIME_BUDGET_S comment above for why: SPI
    calls this once per candidate year, up to `history_years` (4) times,
    and a sequential 5-call-per-year cost is what made the wall-clock
    budget impossible to meet. This is the same ThreadPoolExecutor
    pattern fetch_farm_data() already uses for its own top-level fetches.
    """
    filter_region=_weather_region(lat,lng,polygon,CHIRPS_SCALE_M)
    windows = _month_windows(year)

    def _one_month(window):
        start, end = window
        coll=ee.ImageCollection(CHIRPS_COLLECTION).filterDate(start,end).filterBounds(filter_region).select("precipitation")
        try:
            return _reduce_mean_with_retry(coll.sum(),lat,lng,polygon,CHIRPS_SCALE_M)
        except Exception:
            # Previously swallowed with zero trace — any real cause (auth,
            # quota, transient network error) was invisible in the logs,
            # which is exactly what made "why is SPI null" hard to debug.
            logger.exception("CHIRPS month rainfall fetch failed for %s to %s (year %s)", start, end, year)
            return None

    with ThreadPoolExecutor(max_workers=len(windows)) as pool:
        monthly_values = list(pool.map(_one_month, windows))
    monthly_sums = [v for v in monthly_values if v is not None]
    if not monthly_sums:
        return None
    # Total seasonal rainfall = sum of monthly totals. If a month or two
    # is missing, this slightly understates the season vs a true 5-month
    # total — acceptable given the alternative (the whole season coming
    # back as "no data" because ONE wide aggregation failed) is worse.
    return sum(monthly_sums)

def fetch_spi(lat: float, lng: float, polygon: Optional[dict] = None, current_year: Optional[int] = None, history_years: int = 4) -> Dict[str, Any]:
    start_time = time.monotonic()
    current_year=current_year or _latest_completed_year()
    if current_year>=datetime.utcnow().year: current_year=datetime.utcnow().year-1

    # Current season: try the latest completed year, then fall back one
    # year earlier if that exact window has a gap (mirrors the rainfall
    # and solar-radiation fallback — a real gap in one specific window
    # shouldn't be the reason the whole SPI comes back "no data").
    try:
        used_year = current_year
        current_val = _season_rainfall_mm(lat, lng, polygon, used_year)
        if current_val is None and time.monotonic() - start_time < SPI_TIME_BUDGET_S:
            used_year = current_year - 1
            current_val = _season_rainfall_mm(lat, lng, polygon, used_year)
    except Exception as exc:
        logger.exception("Current-season rainfall fetch for SPI failed")
        return {"available":False,"reason":f"Current-season rainfall fetch failed: {type(exc).__name__}"}
    if current_val is None:
        return {"available":False,"reason":f"No completed CHIRPS v3 rainfall data for {current_year} or {current_year-1}."}

    # History: search backward from the year before the one actually used
    # above, collecting valid years until `history_years` are found, the
    # search-back cap is hit, or the time budget runs out — whichever
    # comes first. This tolerates 1-2 sporadic per-year gaps instead of
    # requiring an exact contiguous block, without letting a sustained
    # outage turn this into a multi-minute call (see SPI_TIME_BUDGET_S).
    history = []
    search_back = history_years + SPI_HISTORY_SEARCH_SLACK
    years_checked = 0
    time_budget_hit = False
    try:
        for y in range(used_year - 1, used_year - 1 - search_back, -1):
            if time.monotonic() - start_time >= SPI_TIME_BUDGET_S:
                time_budget_hit = True
                break
            v = _season_rainfall_mm(lat, lng, polygon, y)
            years_checked += 1
            if v is not None:
                history.append(v)
            if len(history) >= history_years:
                break
    except Exception as exc:
        logger.exception("Year-by-year SPI history fetch failed")
        return {"available":False,"reason":f"Rainfall history fetch failed: {type(exc).__name__}"}
    elapsed = time.monotonic() - start_time
    if len(history)<4:
        reason = f"Insufficient completed rainfall history for SPI (found {len(history)} usable year(s) out of {years_checked} checked"
        reason += f", stopped early after {SPI_TIME_BUDGET_S:.0f}s time budget)." if time_budget_hit else f" out of {search_back} planned)."
        # This is the one log line to check on Render if SPI goes back to
        # "no data": time_budget_hit=True means the budget itself is the
        # bottleneck again (raise SPI_TIME_BUDGET_S further); False with a
        # low years_checked/history ratio means CHIRPS itself has real
        # gaps at this location, not a timing problem.
        logger.warning(
            "fetch_spi: %s (elapsed=%.1fs, time_budget_hit=%s, years_checked=%d, history_found=%d)",
            reason, elapsed, time_budget_hit, years_checked, len(history),
        )
        return {"available":False,"reason":reason}
    current=current_val; mean=sum(history)/len(history); stddev=math.sqrt(sum((x-mean)**2 for x in history)/len(history))
    if stddev==0:return {"available":False,"reason":"Zero variance in rainfall history — cannot compute SPI."}
    spi=round((current-mean)/stddev,2)
    category="Extreme drought" if spi<=-2 else "Severe drought" if spi<=-1.5 else "Moderate drought" if spi<=-1 else "Near normal" if spi<1 else "Moderately wet" if spi<1.5 else "Very wet"
    logger.info("fetch_spi succeeded: spi=%.2f years_used=%d elapsed=%.1fs", spi, len(history), elapsed)
    return {"available":True,"spi":spi,"category":category,"current_season_rainfall_mm":round(current,1),"historical_mean_mm":round(mean,1),"historical_stddev_mm":round(stddev,1),"years_used":len(history),"season_year":used_year,"source":"CHIRPS v3 (Jun-Oct completed-season window)"}

def fetch_gdd(lat: float, lng: float, polygon: Optional[dict] = None, start: Optional[str] = None, end: Optional[str] = None, base_temp_c: float = GDD_BASE_TEMP_C) -> Dict[str, Any]:
    """Growing Degree Days from ERA5-Land 2m air temperature.

    Previously computed from MODIS daytime LST (land *surface* skin
    temperature) — a reasonable stand-in when nothing else was
    reliable, but GDD is conventionally defined using *air*
    temperature, not surface temperature (surface temp swings far
    wider than air temp, especially on bare/dry soil, which skews GDD
    high). Switched to ERA5-Land's temperature_2m, the same dataset and
    band already used for this app's Air Temperature parameter — one
    less independent data source failure mode, and a more correct
    GDD definition.
    """
    try:
        filter_region=_weather_region(lat,lng,polygon,ERA5_LAND_SCALE_M); year=_latest_completed_year()
        if start and end:
            coll=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start,end).filterBounds(filter_region).select("temperature_2m").map(lambda img:img.subtract(273.15).subtract(base_temp_c).max(0).rename("GDD_daily"))
            val=_reduce_mean_with_retry(coll.sum(),lat,lng,polygon,ERA5_LAND_SCALE_M)
            if val is None:return {"available":False,"reason":"GDD reduction returned no value."}
            return {"available":True,"gdd":round(val,1),"base_temp_c":base_temp_c,"window":f"{start} to {end}","note":"Computed from ERA5-Land 2m air temperature (daily mean).","source":"ERA5-Land"}

        # Try the latest completed year first; if EVERY month in that
        # year comes back empty, fall back one year earlier instead of
        # "no data" outright — same fallback pattern already used by
        # fetch_solar_radiation/fetch_spi/fetch_spei_proxy, which this
        # function was missing (a real gap: it only ever tried one year
        # with zero fallback).
        last_reason = "No month in the Jun-Oct window returned a usable ERA5-Land temperature value."
        for candidate_year in (year, year - 1):
            monthly_sums = []
            for m_start, m_end in _month_windows(candidate_year):
                coll=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(m_start,m_end).filterBounds(filter_region).select("temperature_2m").map(lambda img:img.subtract(273.15).subtract(base_temp_c).max(0).rename("GDD_daily"))
                v=_reduce_mean_with_retry(coll.sum(),lat,lng,polygon,ERA5_LAND_SCALE_M)
                if v is not None:
                    monthly_sums.append(v)
            if monthly_sums:
                w_start, w_end = _completed_season_window(candidate_year)
                return {"available":True,"gdd":round(sum(monthly_sums),1),"base_temp_c":base_temp_c,"window":f"{w_start} to {w_end}","months_used":len(monthly_sums),"note":"Computed from ERA5-Land 2m air temperature (daily mean), not MODIS surface temperature.","source":"ERA5-Land"}
            last_reason = f"No month in {candidate_year}'s Jun-Oct window returned a usable ERA5-Land temperature value."
        return {"available":False,"reason":last_reason}
    except Exception as exc:
        logger.exception("GDD fetch failed")
        return {"available":False,"reason":f"GDD computation failed: {type(exc).__name__}"}

def fetch_spei_proxy(lat: float, lng: float, polygon: Optional[dict] = None, current_year: Optional[int] = None) -> Dict[str, Any]:
    current_year=current_year or _latest_completed_year()
    if current_year>=datetime.utcnow().year:current_year=datetime.utcnow().year-1
    chirps_region=_weather_region(lat,lng,polygon,CHIRPS_SCALE_M)
    modis_region=_weather_region(lat,lng,polygon,MODIS_LST_SCALE_M)
    last_reason = f"Insufficient completed-season data for SPEI proxy ({current_year})."
    try:
        # Try the latest completed season first, then fall back one year
        # earlier if THAT year has no usable months in either CHIRPS or
        # MODIS — same fallback pattern as rainfall/solar/SPI above.
        for y in (current_year, current_year - 1):
            rain_monthly, temp_monthly = [], []
            for m_start, m_end in _month_windows(y):
                r=_reduce_mean_with_retry(ee.ImageCollection(CHIRPS_COLLECTION).filterDate(m_start,m_end).filterBounds(chirps_region).select("precipitation").sum(),lat,lng,polygon,CHIRPS_SCALE_M)
                if r is not None:
                    rain_monthly.append(r)
                t=_reduce_mean_with_retry(ee.ImageCollection("MODIS/061/MOD11A1").filterDate(m_start,m_end).filterBounds(modis_region).select("LST_Day_1km").mean().multiply(0.02).subtract(273.15),lat,lng,polygon,MODIS_LST_SCALE_M)
                if t is not None:
                    temp_monthly.append(t)
            if not rain_monthly or not temp_monthly:
                last_reason = f"Insufficient completed-season data for SPEI proxy ({y})."
                continue
            rain = sum(rain_monthly)  # total seasonal rainfall = sum of monthly totals
            temp = sum(temp_monthly) / len(temp_monthly)  # seasonal mean temp = average of monthly means
            if temp<=0:
                last_reason = f"Insufficient completed-season data for SPEI proxy ({y})."
                continue
            heat=(temp/5)**1.514; a=6.75e-7*heat**3-7.71e-5*heat**2+1.792e-2*heat+0.49239; pet=16*((10*temp/heat)**a)*5 if heat>0 else 0
            proxy=round(max(-3,min(3,(rain-pet)/300)),2); category="Dry stress (proxy)" if proxy<=-1.5 else "Excess moisture (proxy)" if proxy>=1.5 else "Near normal (proxy)"
            return {"available":True,"spei_proxy":proxy,"category":category,"rainfall_mm":round(rain,1),"estimated_pet_mm":round(pet,1),"season_year":y,"months_used":min(len(rain_monthly),len(temp_monthly)),"method":"Thornthwaite PET temperature-only proxy; NOT the full standard SPEI.","source":"CHIRPS v3 + MODIS LST"}
        return {"available":False,"reason":last_reason}
    except Exception as exc:
        logger.exception("SPEI proxy fetch failed")
        return {"available":False,"reason":f"SPEI proxy computation failed: {type(exc).__name__}"}


CSIC_SPEI_SCALE_M = 55660  # native pixel size of CSIC/SPEI/2_11 (0.5 degree)


def fetch_spei_index(lat: float, lng: float, polygon: Optional[dict] = None, year: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """SPEI from CSIC's SPEIbase (CSIC/SPEI/2_11) — a purpose-built,
    peer-reviewed SPEI product using the proper FAO-56 Penman-Monteith
    PET method, preferred over fetch_spei_proxy's home-grown Thornthwaite
    approximation whenever it has coverage.

    Two honest trade-offs, always surfaced in the result:
      - ~55.66km pixel size (0.5 degree) — a REGIONAL drought signal,
        not farm-specific. fetch_spei_proxy, whatever its reliability
        problems, is at least computed at the farm's own location.
      - As of this writing CSIC/SPEI/2_11's own data ends 2025-01-01
        (confirmed against the live Earth Engine catalog page) — so for
        a "current season" query in 2026 or later this will typically
        find nothing for the target year and return None, letting the
        caller fall back to the proxy. This is not a bug: querying an
        old year on purpose (backdated analysis) will find data.

    Returns None (never raises) if CSIC has no coverage for this
    year/location, so callers can fall back cleanly.
    """
    year = year or _latest_completed_year()
    try:
        region = _weather_region(lat, lng, polygon, CSIC_SPEI_SCALE_M)
        coll = (
            ee.ImageCollection("CSIC/SPEI/2_11")
            .filterDate(f"{year}-06-01", f"{year}-12-01")
            .select("SPEI_03_month")
            .sort("system:time_start", False)
        )
        if _getinfo_with_backoff(coll.size()) == 0:
            return None
        latest = coll.first()
        val = _reduce_mean_with_retry(latest, lat, lng, polygon, CSIC_SPEI_SCALE_M)
        if val is None:
            return None
        as_of = _getinfo_with_backoff(ee.Date(latest.get("system:time_start")).format("YYYY-MM-dd"))
        category = ("Extreme drought" if val <= -2 else "Severe drought" if val <= -1.5 else
                    "Moderate drought" if val <= -1 else "Near normal" if val < 1 else
                    "Moderately wet" if val < 1.5 else "Very wet")
        return {
            "available": True, "spei_proxy": round(val, 2), "category": category,
            "season_year": year, "as_of": as_of,
            "method": "CSIC SPEIbase, 3-month SPEI — proper FAO-56 Penman-Monteith PET (peer-reviewed), not the Thornthwaite proxy.",
            "resolution_note": "~55.7km regional pixel (0.5°) — a regional drought signal, not specific to this farm.",
            "source": "CSIC/SPEI/2_11",
        }
    except Exception:
        logger.exception("CSIC SPEI fetch failed (non-fatal — falls back to the Thornthwaite proxy)")
        return None


def fetch_spei(lat: float, lng: float, polygon: Optional[dict] = None, current_year: Optional[int] = None) -> Dict[str, Any]:
    """Top-level SPEI entry point. Tries the proper CSIC SPEIbase
    product first (see fetch_spei_index), falls back to the
    Thornthwaite-proxy calculation (fetch_spei_proxy) if CSIC has no
    coverage for this year/location.
    """
    current_year = current_year or _latest_completed_year()
    if current_year >= datetime.utcnow().year:
        current_year = datetime.utcnow().year - 1
    result = fetch_spei_index(lat, lng, polygon, current_year)
    if result is not None:
        return result
    return fetch_spei_proxy(lat, lng, polygon, current_year)
