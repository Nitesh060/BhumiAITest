"""Google Earth Engine data service for FarmScore."""
from __future__ import annotations

import inspect
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import ee

logger = logging.getLogger(__name__)
_ee_initialised = False
_init_lock = threading.Lock()
_cache_lock = threading.Lock()
_coord_cache: Dict[Any, Dict[str, Any]] = {}

SEASON_MONTHS = [8, 9, 10]
LOOKBACK_YEARS = max(1, int(os.getenv("GEE_LOOKBACK_YEARS", "3")))
BUFFER_RADIUS_M = max(10, int(os.getenv("GEE_POINT_FALLBACK_BUFFER_M", "30")))
S2_MAX_CLOUD_PCT = min(80, max(1, int(os.getenv("S2_MAX_CLOUD_PCT", "30"))))
S2_CLOUD_PROBABILITY_MAX = min(100, max(1, int(os.getenv("S2_CLOUD_PROBABILITY_MAX", "40"))))


def _date_window() -> Tuple[str, str]:
    today = date.today()
    return date(today.year - LOOKBACK_YEARS, 8, 1).isoformat(), (today + timedelta(days=1)).isoformat()


def _trend_years() -> Tuple[int, ...]:
    today = date.today()
    return tuple(range(today.year - LOOKBACK_YEARS, today.year + 1))


def _resolve_credentials_path() -> str:
    env_path = os.getenv("GEE_KEY_FILE")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        raise FileNotFoundError(f"GEE_KEY_FILE points to a non-existent file: {env_path}")
    default_path = Path(__file__).resolve().parent / "credentials" / "gee-service-account.json"
    if default_path.is_file():
        return str(default_path)
    raise FileNotFoundError(f"Service-account key not found. Set GEE_KEY_FILE or place the key at {default_path}")


def initialise_earth_engine() -> None:
    global _ee_initialised
    if _ee_initialised:
        return
    with _init_lock:
        if _ee_initialised:
            return
        credentials_json = os.getenv("GOOGLE_CREDENTIALS")
        if credentials_json:
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
                tmp.write(credentials_json)
                key_path = tmp.name
        elif os.getenv("GEE_SERVICE_ACCOUNT") and os.getenv("GEE_PRIVATE_KEY"):
            import tempfile
            key_data = {
                "type": "service_account",
                "client_email": os.getenv("GEE_SERVICE_ACCOUNT"),
                "private_key": os.getenv("GEE_PRIVATE_KEY").replace("\\n", "\n"),
                "project_id": os.getenv("GEE_PROJECT_ID"),
            }
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
                json.dump(key_data, tmp)
                key_path = tmp.name
        else:
            key_path = _resolve_credentials_path()
        with open(key_path, "r", encoding="utf-8") as fh:
            key_data = json.load(fh)
        service_account = key_data.get("client_email")
        if not service_account:
            raise ValueError("client_email missing from service-account key file")
        ee.Initialize(ee.ServiceAccountCredentials(service_account, key_path))
        _ee_initialised = True


def _point_geometry(lat: float, lng: float) -> ee.Geometry.Point:
    return ee.Geometry.Point([lng, lat])


def _buffered_region(lat: float, lng: float, radius_m: int = BUFFER_RADIUS_M) -> ee.Geometry:
    return _point_geometry(lat, lng).buffer(radius_m)


def extract_polygon_coordinates(polygon: Optional[dict]) -> Optional[list]:
    if not polygon:
        return None
    try:
        if isinstance(polygon, dict) and "geometry" in polygon:
            coords = polygon["geometry"]["coordinates"]
        elif isinstance(polygon, dict) and "coordinates" in polygon:
            coords = polygon["coordinates"]
        elif isinstance(polygon, list):
            coords = polygon
        else:
            return None
        return coords[0] if coords and isinstance(coords[0][0], list) else coords
    except Exception:
        logger.exception("Could not extract polygon coordinates")
        return None


def _region_geometry(lat: float, lng: float, polygon: Optional[dict] = None) -> Tuple[ee.Geometry, str]:
    coords = extract_polygon_coordinates(polygon)
    if coords and len(coords) >= 4:
        return ee.Geometry.Polygon([coords]), "parcel_polygon"
    return _buffered_region(lat, lng, BUFFER_RADIUS_M), "approximate_point_buffer"


def _get_region_with_mode(lat: float, lng: float, polygon: Optional[dict] = None) -> Tuple[ee.Geometry, str]:
    return _region_geometry(lat, lng, polygon)


def _get_region(lat: float, lng: float, polygon: Optional[dict] = None):
    """Compatibility helper for both new and legacy modules."""
    region, mode = _region_geometry(lat, lng, polygon)
    caller = inspect.currentframe().f_back.f_globals.get("__file__", "")
    if caller.endswith("earth_engine_service.py") or caller.endswith("spectral_service.py"):
        return region, mode
    return region


def _filter_season(collection: ee.ImageCollection) -> ee.ImageCollection:
    return collection.filter(ee.Filter.calendarRange(8, 10, "month"))


def _filter_growing_season(collection: ee.ImageCollection) -> ee.ImageCollection:
    return _filter_season(collection)


def _reduce_mean(image: ee.Image, region: ee.Geometry, scale: int) -> Optional[float]:
    result = image.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=scale, maxPixels=1e9).getInfo() or {}
    for value in result.values():
        if value is not None:
            return float(value)
    return None


def _sentinel2_cloud_masked(lat: float, lng: float, polygon: Optional[dict]) -> ee.ImageCollection:
    region, _ = _region_geometry(lat, lng, polygon)
    s2 = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
          .filterBounds(region)
          .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT)))
    clouds = ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY").filterBounds(region)
    joined = ee.Join.saveFirst("cloud_probability").apply(
        primary=s2,
        secondary=clouds,
        condition=ee.Filter.equals(leftField="system:index", rightField="system:index"),
    )
    def mask(image):
        cloud_prob = ee.Image(image.get("cloud_probability")).select("probability")
        scl = image.select("SCL")
        scl_ok = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return image.updateMask(cloud_prob.lt(S2_CLOUD_PROBABILITY_MAX)).updateMask(scl_ok)
    return ee.ImageCollection(joined).map(mask)


def _fetch_s2_indices(lat: float, lng: float, polygon: Optional[dict]):
    region, _ = _region_geometry(lat, lng, polygon)
    start, end = _date_window()
    s2 = _filter_season(_sentinel2_cloud_masked(lat, lng, polygon).filterDate(start, end))
    def add_indices(img):
        return img.addBands([
            img.normalizedDifference(["B8", "B4"]).rename("NDVI"),
            img.normalizedDifference(["B8", "B11"]).rename("NDMI"),
            img.normalizedDifference(["B3", "B8"]).rename("NDWI"),
        ])
    mean_img = s2.map(add_indices).select(["NDVI", "NDMI", "NDWI"]).mean()
    result = mean_img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9).getInfo() or {}
    return tuple(float(result[k]) if result.get(k) is not None else None for k in ("NDVI", "NDMI", "NDWI"))


def _latest_completed_climate_year() -> int:
    """Latest full calendar year available for climate-season scoring."""
    return date.today().year - 1


def _completed_climate_window(year: int) -> Tuple[str, str]:
    return f"{year}-06-01", f"{year}-11-01"


def _fetch_rainfall(lat: float, lng: float, polygon: Optional[dict]) -> Optional[float]:
    region, _ = _region_geometry(lat, lng, polygon)
    year = _latest_completed_climate_year()
    start, end = _completed_climate_window(year)
    try:
        c = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
             .filterDate(start, end)
             .filterBounds(region)
             .select("precipitation"))
        if c.size().getInfo() == 0:
            return None
        # CHIRPS precipitation is mm/day. Mean over the completed
        # Jun-Oct season therefore remains a mean daily rainfall value.
        return _reduce_mean(c.mean(), region, 5566)
    except Exception:
        logger.exception("Rainfall fetch failed")
        return None


def _fetch_rainfall_monthly(lat: float, lng: float, polygon: Optional[dict]) -> list:
    region, _ = _region_geometry(lat, lng, polygon)
    year = _latest_completed_climate_year()
    out = []
    for month, label in zip((6, 7, 8, 9, 10), ("Jun", "Jul", "Aug", "Sep", "Oct")):
        try:
            c = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                 .filterDate(f"{year}-{month:02d}-01", f"{year}-{month + 1:02d}-01" if month < 10 else f"{year + 1}-01-01")
                 .filterBounds(region)
                 .select("precipitation"))
            value = _reduce_mean(c.mean(), region, 5566) if c.size().getInfo() else None
        except Exception:
            logger.exception("Rainfall monthly fetch failed for %s", label)
            value = None
        out.append({"month": label, "mm_per_day": round(value, 2) if value is not None else None})
    return out


def _fetch_lst(lat: float, lng: float, polygon: Optional[dict]) -> Optional[float]:
    region, _ = _region_geometry(lat, lng, polygon)
    start, end = _date_window()
    c = _filter_season(ee.ImageCollection("MODIS/061/MOD11A1").filterDate(start, end).filterBounds(region).select("LST_Day_1km"))
    lst_c = c.map(lambda img: img.multiply(0.02).subtract(273.15).rename("LST_C")).mean()
    return _reduce_mean(lst_c, region, 1000)


def _fetch_air_temperature(lat: float, lng: float, polygon: Optional[dict]) -> Optional[float]:
    region, _ = _region_geometry(lat, lng, polygon)
    start, end = _date_window()
    c = _filter_season(ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start, end).filterBounds(region).select("temperature_2m"))
    air_c = c.map(lambda img: img.subtract(273.15).rename("AIR_TEMP_C")).mean()
    return _reduce_mean(air_c, region, 11132)


def _fetch_deep_soil_moisture(lat: float, lng: float, polygon: Optional[dict]) -> Optional[float]:
    region, _ = _region_geometry(lat, lng, polygon)
    start, end = _date_window()
    c = _filter_season(ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H").filterDate(start, end).filterBounds(region).select("SoilMoi100_200cm_inst"))
    return _reduce_mean(c.mean(), region, 27830)


def _fetch_s2_meta(lat: float, lng: float, polygon: Optional[dict]) -> Dict[str, Any]:
    region, _ = _region_geometry(lat, lng, polygon)
    start, end = _date_window()
    c = _filter_season(_sentinel2_cloud_masked(lat, lng, polygon).filterDate(start, end))
    count = c.size().getInfo() or 0
    latest = c.aggregate_max("system:time_start").getInfo() if count else None
    return {"scene_count": int(count), "latest_scene_date": datetime.fromtimestamp(latest / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if latest else None, "cloud_mask": f"S2 cloud probability <= {S2_CLOUD_PROBABILITY_MAX}% + SCL mask"}


def _fetch_ndvi_trend(lat: float, lng: float, polygon: Optional[dict]) -> list:
    region, _ = _region_geometry(lat, lng, polygon)
    out = []
    for year in _trend_years():
        c = _sentinel2_cloud_masked(lat, lng, polygon).filterDate(f"{year}-08-01", f"{year}-11-01")
        ndvi = c.map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")).mean()
        value = _reduce_mean(ndvi, region, 10)
        out.append({"year": year, "ndvi": round(value, 4) if value is not None else None})
    return out


def _fetch_deep_soil_trend(lat: float, lng: float, polygon: Optional[dict]) -> list:
    region, _ = _region_geometry(lat, lng, polygon)
    out = []
    for year in _trend_years():
        c = ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H").filterDate(f"{year}-08-01", f"{year}-11-01").filterBounds(region).select("SoilMoi100_200cm_inst")
        value = _reduce_mean(c.mean(), region, 27830)
        out.append({"year": year, "deep_soil_moisture": round(value, 2) if value is not None else None})
    return out


def fetch_farm_data(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude out of range: {lat}")
    if not (-180 <= lng <= 180):
        raise ValueError(f"Longitude out of range: {lng}")
    initialise_earth_engine()
    region, region_mode = _region_geometry(lat, lng, polygon)
    start_date, end_date = _date_window()
    cache_key = ("polygon", str(polygon)[:1000], start_date, end_date) if polygon else (round(lat, 5), round(lng, 5), start_date, end_date)
    with _cache_lock:
        cached = _coord_cache.get(cache_key)
    if cached:
        return cached.copy()
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_idx = pool.submit(_fetch_s2_indices, lat, lng, polygon)
        f_rain = pool.submit(_fetch_rainfall, lat, lng, polygon)
        f_month = pool.submit(_fetch_rainfall_monthly, lat, lng, polygon)
        f_lst = pool.submit(_fetch_lst, lat, lng, polygon)
        f_air = pool.submit(_fetch_air_temperature, lat, lng, polygon)
        f_soil = pool.submit(_fetch_deep_soil_moisture, lat, lng, polygon)
        f_meta = pool.submit(_fetch_s2_meta, lat, lng, polygon)
        ndvi, ndmi, ndwi = f_idx.result()
        rainfall = f_rain.result()
        rainfall_monthly = f_month.result()
        lst = f_lst.result()
        air_temperature = f_air.result()
        deep_soil_moisture = f_soil.result()
        meta = f_meta.result()
    result = {
        "ndvi": round(ndvi, 6) if ndvi is not None else None,
        "ndmi": round(ndmi, 6) if ndmi is not None else None,
        "ndwi": round(ndwi, 6) if ndwi is not None else None,
        "rainfall": round(rainfall, 4) if rainfall is not None else None,
        "rainfall_monthly": rainfall_monthly,
        "temperature": round(lst, 4) if lst is not None else None,
        "lst": round(lst, 4) if lst is not None else None,
        "air_temperature": round(air_temperature, 4) if air_temperature is not None else None,
        "deep_soil_moisture": round(deep_soil_moisture, 4) if deep_soil_moisture is not None else None,
        "groundwater": round(deep_soil_moisture, 4) if deep_soil_moisture is not None else None,
        "groundwater_label": "Deep Soil Moisture (Groundwater Proxy)",
        "groundwater_trend": _fetch_deep_soil_trend(lat, lng, polygon),
        "satellite_meta": {**meta, "data_window_start": start_date, "data_window_end": end_date, "region_mode": region_mode, "point_fallback_buffer_m": BUFFER_RADIUS_M},
        "ndvi_trend": _fetch_ndvi_trend(lat, lng, polygon),
    }
    with _cache_lock:
        _coord_cache[cache_key] = result
    return result.copy()
