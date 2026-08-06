"""
earth_engine_service.py
========================
Production-ready Google Earth Engine data-retrieval service for FarmScore.

Authenticates with a GEE service account, then queries five satellite /
reanalysis datasets for a single point location over the Aug–Oct growing
seasons of 2020-2023.

Datasets
--------
| Parameter    | Dataset ID                                      | Band(s)                  |
|--------------|-------------------------------------------------|--------------------------|
| NDVI         | COPERNICUS/S2_SR_HARMONIZED                     | B8, B4                   |
| NDMI         | COPERNICUS/S2_SR_HARMONIZED                     | B8, B11                  |
| Rainfall     | UCSB-CHG/CHIRPS/DAILY                           | precipitation            |
| Temperature  | MODIS/061/MOD11A1                               | LST_Day_1km              |
| Groundwater  | NASA/GLDAS/V021/NOAH/G025/T3H                  | SoilMoi100_200cm_inst    |
"""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import ee

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level initialisation guard
# ---------------------------------------------------------------------------
_ee_initialised = False
_init_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
START_DATE = "2020-08-01"
END_DATE = "2023-10-31"

# Growing-season months (August = 8 … October = 10)
SEASON_MONTHS = [8, 9, 10]

# Default buffer radius (metres) around the queried point — keeps reducers
# from returning null on sparse datasets.
BUFFER_RADIUS_M = 500

# Maximum cloud cover percentage for Sentinel-2 scenes
S2_MAX_CLOUD_PCT = 30

# In-memory cache for coordinates to avoid redundant Earth Engine calls.
# Cache key is either a rounded (lat, lng) tuple or a ("polygon", hash) tuple.
_coord_cache: Dict[Tuple[Any, Any], Dict[str, Optional[float]]] = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

def _resolve_credentials_path() -> str:
    """Return the absolute path to the GEE service-account key file.

    Resolution order:
      1. ``GEE_KEY_FILE`` environment variable (explicit override).
      2. ``credentials/gee-service-account.json`` relative to this file.

    Raises ``FileNotFoundError`` if neither resolves to an existing file.
    """
    env_path = os.getenv("GEE_KEY_FILE")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return str(p)
        raise FileNotFoundError(
            f"GEE_KEY_FILE points to a non-existent file: {env_path}"
        )

    default_path = Path(__file__).resolve().parent / "credentials" / "gee-service-account.json"
    if default_path.is_file():
        return str(default_path)

    raise FileNotFoundError(
        "Service-account key not found. Set GEE_KEY_FILE or place the key at "
        f"{default_path}"
    )


def initialise_earth_engine() -> None:
    """Initialise Earth Engine with service-account credentials (idempotent).

    Thread-safe — multiple concurrent Flask requests will not race.
    """
    global _ee_initialised
    if _ee_initialised:
        return

    with _init_lock:
        if _ee_initialised:          # double-checked locking
            return

        credentials_json = os.getenv("GOOGLE_CREDENTIALS")

        if credentials_json:
            import tempfile

            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(credentials_json)
                key_path = tmp.name
        else:
            key_path = _resolve_credentials_path()

        logger.info("Authenticating Earth Engine with key: %s", key_path)

        with open(key_path, "r", encoding="utf-8") as fh:
            key_data = json.load(fh)

        service_account = key_data.get("client_email")
        if not service_account:
            raise ValueError("client_email missing from service-account key file")

        credentials = ee.ServiceAccountCredentials(service_account, key_path)
        ee.Initialize(credentials)
        logger.info("Earth Engine initialised for account: %s", service_account)
        _ee_initialised = True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _point_geometry(lat: float, lng: float) -> ee.Geometry.Point:
    """Create an ``ee.Geometry.Point`` (longitude first, as GEE expects)."""
    return ee.Geometry.Point([lng, lat])


def _buffered_region(lat: float, lng: float, radius_m: int = BUFFER_RADIUS_M) -> ee.Geometry:
    """Return a circular buffer around the point to guard against sparse pixels."""
    return _point_geometry(lat, lng).buffer(radius_m)


def extract_polygon_coordinates(polygon: Optional[dict]) -> Optional[list]:
    """Normalizes any of the polygon shapes this app has produced over
    time into a plain coordinate ring:
      - Leaflet's layer.toGeoJSON() output: {"type":"Feature","geometry":{"coordinates":[[...]]}}
      - A bare GeoJSON Polygon geometry: {"type":"Polygon","coordinates":[[...]]}
      - Already-unwrapped coordinates: [[[lng,lat],...]]
    Returns the outer ring as [[lng,lat],...], or None if nothing usable was found.
    Every function that accepts a `polygon` argument should go through
    this instead of reaching into polygon["coordinates"] or
    polygon["geometry"]["coordinates"] directly — the two formats above
    have both been in use across this codebase and picking the wrong
    one silently drops the farm boundary (falls back to a buffered point).
    """
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
        # coords may be [[[lng,lat],...]] (standard Polygon) — take the outer ring
        return coords[0] if coords and isinstance(coords[0][0], list) else coords
    except Exception:
        logger.exception("Could not extract polygon coordinates — falling back to point-only")
        return None


def _get_region(lat: float, lng: float, polygon: Optional[dict] = None) -> ee.Geometry:
    """Return a Polygon geometry if one was supplied, otherwise a buffered point."""
    coords = extract_polygon_coordinates(polygon)
    if coords:
        return ee.Geometry.Polygon([coords])

    return _buffered_region(lat, lng)


def _filter_growing_season(collection: ee.ImageCollection) -> ee.ImageCollection:
    """Restrict an ImageCollection to calendar months 8-10 across all years."""
    return collection.filter(ee.Filter.calendarRange(8, 10, "month"))


def _reduce_mean(image: ee.Image, region: ee.Geometry, scale: int) -> Optional[float]:
    """Reduce an image to its mean value over *region* at the given scale.

    Returns ``None`` if the reducer yields no data (e.g. ocean pixels).
    """
    result: Dict[str, Any] = (
        image
        .reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=scale,
            maxPixels=1e9,
        )
        .getInfo()
    )
    # Return the first non-null value found
    for val in result.values():
        if val is not None:
            return float(val)
    return None


# ---------------------------------------------------------------------------
# Dataset fetch functions
# ---------------------------------------------------------------------------

def _fetch_s2_indices(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Mean NDVI, NDMI and NDWI from Sentinel-2 Surface Reflectance (Harmonized) in a single query.

    NDVI = (B8 – B4)  / (B8 + B4)    — vegetation health
    NDMI = (B8 – B11) / (B8 + B11)   — vegetation/canopy moisture
    NDWI = (B3 – B8)  / (B3 + B8)    — surface water content (McFeeters)
    """
    region = _get_region(lat, lng, polygon)
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT))
    )
    s2 = _filter_growing_season(s2)

    def compute_indices(img: ee.Image) -> ee.Image:
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndmi = img.normalizedDifference(["B8", "B11"]).rename("NDMI")
        ndwi = img.normalizedDifference(["B3", "B8"]).rename("NDWI")
        return img.addBands([ndvi, ndmi, ndwi])

    indices_collection = s2.map(compute_indices)
    mean_indices = indices_collection.select(["NDVI", "NDMI", "NDWI"]).mean()

    # Perform a single reduceRegion call for all three bands
    result = (
        mean_indices
        .reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=10,
            maxPixels=1e9,
        )
        .getInfo()
    )

    ndvi_val = float(result["NDVI"]) if result and result.get("NDVI") is not None else None
    ndmi_val = float(result["NDMI"]) if result and result.get("NDMI") is not None else None
    ndwi_val = float(result["NDWI"]) if result and result.get("NDWI") is not None else None
    return ndvi_val, ndmi_val, ndwi_val


def _fetch_rainfall(lat: float, lng: float, polygon: Optional[dict] = None) -> Optional[float]:
    """Mean daily precipitation (mm/day) from CHIRPS Daily dataset.

    Returns the temporal mean of daily precipitation values over the
    growing-season windows.
    """
    region = _get_region(lat, lng, polygon)
    chirps = (
        ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .select("precipitation")
    )
    chirps = _filter_growing_season(chirps)

    mean_precip = chirps.mean()
    return _reduce_mean(mean_precip, region, scale=5566)


def _fetch_rainfall_monthly(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> list:
    """Real month-by-month mean daily rainfall (mm/day) for Aug/Sep/Oct,
    averaged across 2020-2023 — same CHIRPS source as _fetch_rainfall,
    broken down instead of collapsed into one number.
    """
    region = _get_region(lat, lng, polygon)
    monthly = []

    for month in SEASON_MONTHS:
        chirps_month = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(START_DATE, END_DATE)
            .filterBounds(region)
            .filter(ee.Filter.calendarRange(month, month, "month"))
            .select("precipitation")
        )
        val = _reduce_mean(chirps_month.mean(), region, scale=5566)
        monthly.append({
            "month": ["Aug", "Sep", "Oct"][SEASON_MONTHS.index(month)],
            "mm_per_day": round(val, 2) if val is not None else None,
        })

    return monthly


def _fetch_temperature(lat: float, lng: float, polygon: Optional[dict] = None) -> Optional[float]:
    """Mean daytime Land Surface Temperature (°C) from MODIS/061/MOD11A1.

    The raw band stores LST in Kelvin × 0.02.  We apply the scale factor
    and convert to Celsius.
    """
    region = _get_region(lat, lng, polygon)
    modis = (
        ee.ImageCollection("MODIS/061/MOD11A1")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .select("LST_Day_1km")
    )
    modis = _filter_growing_season(modis)

    # Scale: DN × 0.02 → Kelvin, then K – 273.15 → °C
    lst_celsius = modis.map(
        lambda img: img.multiply(0.02).subtract(273.15).rename("LST_C")
    )
    mean_lst = lst_celsius.mean()
    return _reduce_mean(mean_lst, region, scale=1000)


def _fetch_groundwater(lat: float, lng: float, polygon: Optional[dict] = None) -> Optional[float]:
    """Mean deep-layer soil moisture (kg/m²) from GLDAS Noah v2.1.

    Uses ``SoilMoi100_200cm_inst`` (100-200 cm layer) as a proxy for
    groundwater storage.
    """
    region = _get_region(lat, lng, polygon)
    gldas = (
        ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .select("SoilMoi100_200cm_inst")
    )
    gldas = _filter_growing_season(gldas)

    mean_gw = gldas.mean()
    return _reduce_mean(mean_gw, region, scale=27830)


# ---------------------------------------------------------------------------
# Satellite metadata + historical trend
# ---------------------------------------------------------------------------

def _fetch_s2_meta(
    lat: float, lng: float, polygon: Optional[dict] = None
) -> Dict[str, Any]:
    """Real metadata about the Sentinel-2 scenes used in the composite:
    how many scenes went in, their mean cloud cover, and the most
    recent scene date. Not a "live" single image — this dataset is a
    multi-year growing-season composite (see START_DATE/END_DATE).
    """
    region = _get_region(lat, lng, polygon)
    s2 = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate(START_DATE, END_DATE)
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT))
    )
    s2 = _filter_growing_season(s2)

    count = s2.size().getInfo()
    if not count:
        return {"scene_count": 0, "mean_cloud_cover": None, "latest_scene_date": None}

    mean_cloud = s2.aggregate_mean("CLOUDY_PIXEL_PERCENTAGE").getInfo()
    latest_ts = s2.aggregate_max("system:time_start").getInfo()

    latest_date = None
    if latest_ts:
        from datetime import datetime, timezone
        latest_date = datetime.fromtimestamp(
            latest_ts / 1000, tz=timezone.utc
        ).strftime("%Y-%m-%d")

    return {
        "scene_count": int(count),
        "mean_cloud_cover": round(mean_cloud, 1) if mean_cloud is not None else None,
        "latest_scene_date": latest_date,
    }


def _fetch_ndvi_trend(
    lat: float,
    lng: float,
    polygon: Optional[dict] = None,
    years: Tuple[int, ...] = (2020, 2021, 2022, 2023),
) -> list:
    """Mean growing-season NDVI per year — real per-year composites from
    the same Sentinel-2 collection, not a fabricated series.
    """
    region = _get_region(lat, lng, polygon)
    trend = []

    for year in years:
        s2_year = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(f"{year}-08-01", f"{year}-10-31")
            .filterBounds(region)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT))
        )
        ndvi_img = (
            s2_year
            .map(lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI"))
            .select("NDVI")
            .mean()
        )
        val = _reduce_mean(ndvi_img, region, scale=10)
        trend.append({"year": year, "ndvi": round(val, 4) if val is not None else None})

    return trend


def _fetch_groundwater_trend(
    lat: float,
    lng: float,
    polygon: Optional[dict] = None,
    years: Tuple[int, ...] = (2020, 2021, 2022, 2023),
) -> list:
    """Mean growing-season groundwater proxy (kg/m²) per year — real
    per-year composites from the same GLDAS collection.
    """
    region = _get_region(lat, lng, polygon)
    trend = []

    for year in years:
        gldas_year = (
            ee.ImageCollection("NASA/GLDAS/V021/NOAH/G025/T3H")
            .filterDate(f"{year}-08-01", f"{year}-10-31")
            .filterBounds(region)
            .select("SoilMoi100_200cm_inst")
        )
        val = _reduce_mean(gldas_year.mean(), region, scale=27830)
        trend.append({"year": year, "groundwater": round(val, 2) if val is not None else None})

    return trend


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_farm_data(
    lat: float,
    lng: float,
    polygon: Optional[dict] = None,
) -> Dict[str, Optional[float]]:
    """Fetch all five agricultural parameters for a single coordinate.

    Parameters
    ----------
    lat : float
        Latitude in decimal degrees (−90 to 90).
    lng : float
        Longitude in decimal degrees (−180 to 180).
    polygon : dict, optional
        A GeoJSON-like feature with a Polygon geometry. When supplied,
        this is used as the query region instead of a buffered point.

    Returns
    -------
    dict
        Keys: ``ndvi``, ``ndmi``, ``rainfall``, ``temperature``,
        ``groundwater``.  Values are floats or ``None`` when no data
        is available for that parameter at the queried location.

    Raises
    ------
    ValueError
        If the coordinates are out of range.
    """
    # ---- Validate inputs ----
    if not (-90 <= lat <= 90):
        raise ValueError(f"Latitude out of range: {lat}")
    if not (-180 <= lng <= 180):
        raise ValueError(f"Longitude out of range: {lng}")

    # ---- Check cache ----
    if polygon:
        cache_key: Tuple[Any, Any] = ("polygon", str(polygon)[:200])
    else:
        cache_key = (round(lat, 5), round(lng, 5))

    with _cache_lock:
        if cache_key in _coord_cache:
            logger.info("Cache hit for coordinates: %s -> %s", (lat, lng), cache_key)
            return _coord_cache[cache_key].copy()

    # ---- Ensure EE is ready ----
    initialise_earth_engine()

    # ---- Fetch each parameter ----
    if polygon:
        logger.info("Fetching Earth Engine data for polygon region")
    else:
        logger.info("Fetching Earth Engine data for (%.5f, %.5f) …", lat, lng)

    # ---- Fetch all parameters concurrently ----
    # Each of these makes one or more independent Earth Engine network
    # calls (.getInfo()). Running them one after another was the main
    # reason /calculate got slow as more real-data features were added.
    # They don't depend on each other, so a thread pool lets them
    # overlap — .getInfo() is blocking network I/O and releases the
    # GIL while waiting, so this is a genuine wall-clock speedup, not
    # just cosmetic.
    with ThreadPoolExecutor(max_workers=4) as pool:
        f_indices = pool.submit(_fetch_s2_indices, lat, lng, polygon)
        f_rainfall = pool.submit(_fetch_rainfall, lat, lng, polygon)
        f_rainfall_monthly = pool.submit(_fetch_rainfall_monthly, lat, lng, polygon)
        f_temperature = pool.submit(_fetch_temperature, lat, lng, polygon)
        f_groundwater = pool.submit(_fetch_groundwater, lat, lng, polygon)
        f_groundwater_trend = pool.submit(_fetch_groundwater_trend, lat, lng, polygon)
        f_satellite_meta = pool.submit(_fetch_s2_meta, lat, lng, polygon)
        f_ndvi_trend = pool.submit(_fetch_ndvi_trend, lat, lng, polygon)

        ndvi, ndmi, ndwi = f_indices.result()
        rainfall = f_rainfall.result()
        rainfall_monthly = f_rainfall_monthly.result()
        temperature = f_temperature.result()
        groundwater = f_groundwater.result()
        groundwater_trend = f_groundwater_trend.result()
        satellite_meta = f_satellite_meta.result()
        ndvi_trend = f_ndvi_trend.result()

    logger.debug("  NDVI: %s  NDMI: %s  NDWI: %s", ndvi, ndmi, ndwi)
    logger.debug("  Rainfall: %s mm/day  monthly: %s", rainfall, rainfall_monthly)
    logger.debug("  Temperature: %s °C", temperature)
    logger.debug("  Groundwater: %s kg/m²  trend: %s", groundwater, groundwater_trend)
    logger.debug("  Satellite meta: %s", satellite_meta)
    logger.debug("  NDVI trend: %s", ndvi_trend)

    result = {
        "ndvi": round(ndvi, 6) if ndvi is not None else None,
        "ndmi": round(ndmi, 6) if ndmi is not None else None,
        "ndwi": round(ndwi, 6) if ndwi is not None else None,
        "rainfall": round(rainfall, 4) if rainfall is not None else None,
        "rainfall_monthly": rainfall_monthly,
        "temperature": round(temperature, 4) if temperature is not None else None,
        "groundwater": round(groundwater, 4) if groundwater is not None else None,
        "groundwater_trend": groundwater_trend,
        "satellite_meta": satellite_meta,
        "ndvi_trend": ndvi_trend,
    }

    with _cache_lock:
        _coord_cache[cache_key] = result

    return result.copy()
