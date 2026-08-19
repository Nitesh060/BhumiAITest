"""Weather indices used by the FarmScore comprehensive score."""
from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Dict, Optional
import ee
from earth_engine_service import (
    _reduce_mean_with_retry,
    _scaled_region,
    CHIRPS_SCALE_M,
    MODIS_LST_SCALE_M,
    ERA5_LAND_SCALE_M,
)

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

def _latest_completed_year() -> int:
    return datetime.utcnow().year - 1

def fetch_solar_radiation(lat: float, lng: float, polygon: Optional[dict] = None, start: Optional[str] = None, end: Optional[str] = None) -> Dict[str, Any]:
    try:
        filter_region=_weather_region(lat,lng,polygon,ERA5_LAND_SCALE_M); year=_latest_completed_year(); start,end=(start,end) if start and end else _completed_season_window(year)
        coll=ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start,end).filterBounds(filter_region).select("surface_solar_radiation_downwards_sum")
        val=_reduce_mean_with_retry(coll.mean(),lat,lng,polygon,ERA5_LAND_SCALE_M)
        if val is None:
            return {"available":False,"reason":"ERA5-Land solar radiation reduction returned no value."}
        return {"available":True,"avg_daily_solar_radiation_mj_m2":round(max(0.0,val)/1_000_000,2),"window":f"{start} to {end}","source":"ECMWF ERA5-Land Daily Aggregate"}
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
    start,end=_completed_season_window(year)
    filter_region=_weather_region(lat,lng,polygon,CHIRPS_SCALE_M)
    coll=ee.ImageCollection(CHIRPS_COLLECTION).filterDate(start,end).filterBounds(filter_region).select("precipitation")
    try:
        return _reduce_mean_with_retry(coll.sum(),lat,lng,polygon,CHIRPS_SCALE_M)
    except Exception:
        return None

def fetch_spi(lat: float, lng: float, polygon: Optional[dict] = None, current_year: Optional[int] = None, history_years: int = 4) -> Dict[str, Any]:
    current_year=current_year or _latest_completed_year()
    if current_year>=datetime.utcnow().year: current_year=datetime.utcnow().year-1
    start_year=current_year-history_years
    try:
        annual=[(y,_season_rainfall_mm(lat,lng,polygon,y)) for y in range(start_year,current_year+1)]
    except Exception as exc:
        logger.exception("Year-by-year SPI fetch failed")
        return {"available":False,"reason":f"Rainfall history fetch failed: {type(exc).__name__}"}
    valid=[(y,v) for y,v in annual if v is not None]
    current_pair=next(((y,v) for y,v in valid if y==current_year),None)
    if current_pair is None:return {"available":False,"reason":f"No completed CHIRPS v3 rainfall data for {current_year}."}
    history=[v for y,v in valid if y<current_year]
    if len(history)<4:return {"available":False,"reason":"Insufficient completed rainfall history for SPI."}
    current=current_pair[1]; mean=sum(history)/len(history); stddev=math.sqrt(sum((x-mean)**2 for x in history)/len(history))
    if stddev==0:return {"available":False,"reason":"Zero variance in rainfall history — cannot compute SPI."}
    spi=round((current-mean)/stddev,2)
    category="Extreme drought" if spi<=-2 else "Severe drought" if spi<=-1.5 else "Moderate drought" if spi<=-1 else "Near normal" if spi<1 else "Moderately wet" if spi<1.5 else "Very wet"
    return {"available":True,"spi":spi,"category":category,"current_season_rainfall_mm":round(current,1),"historical_mean_mm":round(mean,1),"historical_stddev_mm":round(stddev,1),"years_used":len(history),"season_year":current_year,"source":"CHIRPS v3 (Jun-Oct completed-season window)"}

def fetch_gdd(lat: float, lng: float, polygon: Optional[dict] = None, start: Optional[str] = None, end: Optional[str] = None, base_temp_c: float = GDD_BASE_TEMP_C) -> Dict[str, Any]:
    try:
        filter_region=_weather_region(lat,lng,polygon,MODIS_LST_SCALE_M); year=_latest_completed_year(); start,end=(start,end) if start and end else _completed_season_window(year)
        coll=ee.ImageCollection("MODIS/061/MOD11A1").filterDate(start,end).filterBounds(filter_region).select("LST_Day_1km").map(lambda img:img.multiply(0.02).subtract(273.15).subtract(base_temp_c).max(0).rename("GDD_daily"))
        val=_reduce_mean_with_retry(coll.sum(),lat,lng,polygon,MODIS_LST_SCALE_M)
        if val is None:return {"available":False,"reason":"GDD reduction returned no value."}
        return {"available":True,"gdd":round(val,1),"base_temp_c":base_temp_c,"window":f"{start} to {end}","note":"Computed from MODIS daytime LST, not true air temperature — an approximation.","source":"MODIS LST"}
    except Exception as exc:
        logger.exception("GDD fetch failed")
        return {"available":False,"reason":f"GDD computation failed: {type(exc).__name__}"}

def fetch_spei_proxy(lat: float, lng: float, polygon: Optional[dict] = None, current_year: Optional[int] = None) -> Dict[str, Any]:
    current_year=current_year or _latest_completed_year()
    if current_year>=datetime.utcnow().year:current_year=datetime.utcnow().year-1
    chirps_region=_weather_region(lat,lng,polygon,CHIRPS_SCALE_M)
    modis_region=_weather_region(lat,lng,polygon,MODIS_LST_SCALE_M)
    try:
        rain=_reduce_mean_with_retry(ee.ImageCollection(CHIRPS_COLLECTION).filterDate(f"{current_year}-06-01",f"{current_year}-11-01").filterBounds(chirps_region).select("precipitation").sum(),lat,lng,polygon,CHIRPS_SCALE_M)
        temp=_reduce_mean_with_retry(ee.ImageCollection("MODIS/061/MOD11A1").filterDate(f"{current_year}-06-01",f"{current_year}-11-01").filterBounds(modis_region).select("LST_Day_1km").mean().multiply(0.02).subtract(273.15),lat,lng,polygon,MODIS_LST_SCALE_M)
        if rain is None or temp is None or temp<=0:return {"available":False,"reason":f"Insufficient completed-season data for SPEI proxy ({current_year})."}
        heat=(temp/5)**1.514; a=6.75e-7*heat**3-7.71e-5*heat**2+1.792e-2*heat+0.49239; pet=16*((10*temp/heat)**a)*5 if heat>0 else 0
        proxy=round(max(-3,min(3,(rain-pet)/300)),2); category="Dry stress (proxy)" if proxy<=-1.5 else "Excess moisture (proxy)" if proxy>=1.5 else "Near normal (proxy)"
        return {"available":True,"spei_proxy":proxy,"category":category,"rainfall_mm":round(rain,1),"estimated_pet_mm":round(pet,1),"season_year":current_year,"method":"Thornthwaite PET temperature-only proxy; NOT the full standard SPEI.","source":"CHIRPS v3 + MODIS LST"}
    except Exception as exc:
        logger.exception("SPEI proxy fetch failed")
        return {"available":False,"reason":f"SPEI proxy computation failed: {type(exc).__name__}"}
