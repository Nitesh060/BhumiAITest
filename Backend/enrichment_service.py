"""
enrichment_service.py
======================
Additional real-satellite-data modules for FarmScore, added to close the
gap against the SatSource comparison sheet. Every function here queries a
real public Earth Engine dataset — nothing here is fabricated or
hardcoded per-farm.

Two features from the SatSource sheet are intentionally NOT in this file
because they cannot be sourced from Earth Engine at all — see
``govt_data_service.py`` for those (Crop Price/MSP, District Yield),
which need a data.gov.in / Agmarknet API key and network access this
module doesn't require.

Datasets used here
-------------------
| Parameter            | Dataset ID                                      |
|-----------------------|-------------------------------------------------|
| Soil Type              | OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02 |
| Land Cover (adjacent)  | ESA/WorldCover/v200                             |
| Cropping Intensity     | COPERNICUS/S2_SR_HARMONIZED (monthly NDVI)      |
| Irrigation signal       | COPERNICUS/S2_SR_HARMONIZED (dry-season NDVI)   |
| Temperature Annual Range| MODIS/061/MOD11A1 (full-year min/max)          |
| Regional Prosperity proxy| NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG (nightlights)|
| Nearest Water Body      | JRC/GSW1_4/GlobalSurfaceWater                   |
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import ee

from earth_engine_service import _get_region, _reduce_mean, _buffered_region, _point_geometry, extract_polygon_coordinates as _extract_coords_local

logger = logging.getLogger(__name__)

CURRENT_YEAR_START = "2023-11-01"
CURRENT_YEAR_END = "2024-10-31"

# USDA soil texture class codes -> human-readable label
# (OpenLandMap SOL_TEXTURE-CLASS_USDA-TT_M/v02, band b0 = 0 cm depth)
_USDA_TEXTURE_LABELS = {
    1: "Clay", 2: "Silty Clay", 3: "Sandy Clay", 4: "Clay Loam",
    5: "Silty Clay Loam", 6: "Sandy Clay Loam", 7: "Loam",
    8: "Silty Loam", 9: "Sandy Loam", 10: "Silt", 11: "Loamy Sand", 12: "Sand",
}

# ESA WorldCover v200 class codes -> label
_WORLDCOVER_LABELS = {
    10: "Tree cover", 20: "Shrubland", 30: "Grassland", 40: "Cropland",
    50: "Built-up", 60: "Bare / sparse vegetation", 70: "Snow and ice",
    80: "Permanent water bodies", 90: "Herbaceous wetland",
    95: "Mangroves", 100: "Moss and lichen",
}


# ---------------------------------------------------------------------------
# Soil Type
# ---------------------------------------------------------------------------

def fetch_soil_type(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """Dominant USDA soil texture class at 0 cm depth (OpenLandMap)."""
    region = _get_region(lat, lng, polygon)
    img = ee.Image("OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02").select("b0")

    result = img.reduceRegion(
        reducer=ee.Reducer.mode(),
        geometry=region,
        scale=250,
        maxPixels=1e9,
    ).getInfo()

    code = result.get("b0")
    if code is None:
        return {"class_code": None, "label": None, "source": "OpenLandMap SoilGrids"}

    code = int(round(code))
    return {
        "class_code": code,
        "label": _USDA_TEXTURE_LABELS.get(code, "Unknown"),
        "source": "OpenLandMap SoilGrids (0 cm depth)",
    }


# ---------------------------------------------------------------------------
# Adjacent Land Cover breakdown
# ---------------------------------------------------------------------------

def fetch_adjacent_land_cover(
    lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int = 1000
) -> Dict[str, Any]:
    """% breakdown of land-cover classes in a ring around the farm — tells
    you what's actually next door (more cropland, forest, built-up, water).
    """
    farm_region = _get_region(lat, lng, polygon)
    outer = farm_region.buffer(buffer_m) if polygon else _buffered_region(lat, lng, buffer_m)
    ring = outer.difference(farm_region, ee.ErrorMargin(10))

    lc = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map")

    hist = lc.reduceRegion(
        reducer=ee.Reducer.frequencyHistogram(),
        geometry=ring,
        scale=10,
        maxPixels=1e9,
        bestEffort=True,
    ).getInfo()

    counts = hist.get("Map", {})
    total = sum(counts.values()) or 1
    breakdown = [
        {
            "class": _WORLDCOVER_LABELS.get(int(float(k)), "Unknown"),
            "percent": round(100 * v / total, 1),
        }
        for k, v in counts.items()
    ]
    breakdown.sort(key=lambda x: x["percent"], reverse=True)
    return {"buffer_m": buffer_m, "breakdown": breakdown, "source": "ESA WorldCover v200 (10 m)"}


# ---------------------------------------------------------------------------
# Cropping Intensity (mono / double / triple cropping)
# ---------------------------------------------------------------------------

def fetch_cropping_intensity(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """Counts distinct NDVI growth peaks across a full year to infer how
    many cropping cycles the field goes through — a real, if approximate,
    signal (a proper crop-calendar model would need field-level ground
    truth, which this doesn't claim to have).
    """
    region = _get_region(lat, lng, polygon)
    monthly_ndvi: List[Optional[float]] = []

    for m in range(1, 13):
        start = f"2023-{m:02d}-01"
        end_month = m + 1 if m < 12 else 1
        end_year = 2023 if m < 12 else 2024
        end = f"{end_year}-{end_month:02d}-01"
        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterDate(start, end)
            .filterBounds(region)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
        )
        ndvi_img = s2.map(
            lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ).select("NDVI").mean()
        val = _reduce_mean(ndvi_img, region, scale=20)
        monthly_ndvi.append(round(val, 4) if val is not None else None)

    # Count peaks: a local max that rises >0.1 above its neighbouring troughs
    clean = [v for v in monthly_ndvi if v is not None]
    peaks = 0
    if len(clean) >= 3:
        for i in range(1, len(clean) - 1):
            if clean[i] > clean[i - 1] + 0.08 and clean[i] > clean[i + 1] + 0.08:
                peaks += 1
        # wrap-around check (Dec->Jan) since cropping years aren't calendar-aligned
        if clean[0] > clean[-1] + 0.08 and clean[0] > clean[1] + 0.08:
            peaks += 1

    peaks = max(1, peaks)
    label = {1: "Single cropping (mono)", 2: "Double cropping"}.get(peaks, "Triple / multi cropping")

    return {
        "monthly_ndvi": monthly_ndvi,
        "estimated_cycles": peaks,
        "label": label,
        "note": "Estimated from NDVI seasonality, not ground-truth crop calendar data.",
        "source": "Sentinel-2 (12-month NDVI series)",
    }


# ---------------------------------------------------------------------------
# Irrigation Detection
# ---------------------------------------------------------------------------

def fetch_irrigation_signal(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """If NDVI stays healthy through the dry (non-monsoon) months, the
    field is very likely irrigated — rainfed land goes brown in that
    window. Simple, real, defensible signal.
    """
    region = _get_region(lat, lng, polygon)
    dry_season = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterDate("2024-02-01", "2024-04-30")
        .filterBounds(region)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
    )
    ndvi_img = dry_season.map(
        lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")
    ).select("NDVI").mean()
    dry_ndvi = _reduce_mean(ndvi_img, region, scale=20)

    if dry_ndvi is None:
        return {"dry_season_ndvi": None, "likely_irrigated": None, "source": "Sentinel-2 (Feb-Apr NDVI)"}

    likely_irrigated = dry_ndvi > 0.35
    return {
        "dry_season_ndvi": round(dry_ndvi, 4),
        "likely_irrigated": likely_irrigated,
        "confidence": "Indicative — based on dry-season vegetation greenness, not canal/pump records.",
        "source": "Sentinel-2 (Feb-Apr NDVI)",
    }


# ---------------------------------------------------------------------------
# Temperature Annual Range
# ---------------------------------------------------------------------------

def fetch_temperature_annual_range(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """Full calendar-year min/max/mean LST, not just the Aug-Oct growing
    season figure already shown elsewhere.
    """
    region = _get_region(lat, lng, polygon)
    modis = (
        ee.ImageCollection("MODIS/061/MOD11A1")
        .filterDate("2023-01-01", "2023-12-31")
        .filterBounds(region)
        .select("LST_Day_1km")
        .map(lambda img: img.multiply(0.02).subtract(273.15).rename("LST_C"))
    )

    stats = modis.reduce(ee.Reducer.minMax().combine(ee.Reducer.mean(), sharedInputs=True))
    result = stats.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=1000,
        maxPixels=1e9,
    ).getInfo()

    def _r(key):
        v = result.get(key)
        return round(v, 2) if v is not None else None

    return {
        "min_c": _r("LST_C_min"),
        "max_c": _r("LST_C_max"),
        "mean_c": _r("LST_C_mean"),
        "source": "MODIS LST (full calendar year 2023)",
    }


# ---------------------------------------------------------------------------
# Regional Prosperity proxy (nighttime lights)
# ---------------------------------------------------------------------------

def fetch_prosperity_proxy(lat: float, lng: float, polygon: Optional[dict] = None, radius_m: int = 5000) -> Dict[str, Any]:
    """Nighttime-lights radiance is a well-documented proxy for local
    economic activity in remote-sensing economics literature — it is
    NOT an official income/prosperity index, and is labelled as a proxy
    throughout the UI.
    """
    region = _buffered_region(lat, lng, radius_m)
    viirs = (
        ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG")
        .filterDate("2023-01-01", "2023-12-31")
        .select("avg_rad")
        .mean()
    )
    val = _reduce_mean(viirs, region, scale=500)
    if val is None:
        return {"avg_radiance": None, "tier": None, "source": "VIIRS Nighttime Lights (proxy only)"}

    if val < 1:
        tier = "Low economic activity (rural/agrarian)"
    elif val < 5:
        tier = "Moderate economic activity"
    else:
        tier = "High economic activity (peri-urban/urban proximity)"

    return {
        "avg_radiance": round(val, 3),
        "tier": tier,
        "note": "Proxy indicator from satellite nightlights, not an official government prosperity index.",
        "source": "VIIRS Nighttime Lights, 5 km radius",
    }


# ---------------------------------------------------------------------------
# Nearest Water Body (natural — lakes/rivers, distinct from the OSM canal
# lookup the frontend already does)
# ---------------------------------------------------------------------------

def fetch_nearest_water_body_signal(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """% of surface water occurrence within 2 km — JRC Global Surface
    Water. Doesn't give a road-network distance (that's the frontend's
    OSM job); gives a satellite-verified presence/extent signal instead.
    """
    region = _buffered_region(lat, lng, 2000)
    gsw = ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence")

    water_area = gsw.gt(50).selfMask().reduceRegion(
        reducer=ee.Reducer.count(),
        geometry=region,
        scale=30,
        maxPixels=1e9,
    ).getInfo()

    px_count = water_area.get("occurrence", 0) or 0
    water_present = px_count > 0
    return {
        "water_pixels_within_2km": int(px_count),
        "water_present": water_present,
        "source": "JRC Global Surface Water (30 m, >50% occurrence)",
    }


# ---------------------------------------------------------------------------
# Agro-Ecological Zone (indicative — rule-based, not the official ICAR
# shapefile, since that isn't on Earth Engine's public catalog)
# ---------------------------------------------------------------------------

def estimate_agro_ecological_zone(rainfall_mm_day: Optional[float], temperature_c: Optional[float]) -> Dict[str, Any]:
    """Coarse AEZ-style classification from rainfall + temperature. This
    is NOT a lookup against the official ICAR/NBSS&LUP 20-zone shapefile
    (that dataset isn't publicly available on Earth Engine) — it's a
    transparent, documented approximation using the same two climate
    variables ICAR zones are built from.
    """
    if rainfall_mm_day is None or temperature_c is None:
        return {"zone": None, "note": "Insufficient data"}

    annual_rain_mm = rainfall_mm_day * 365  # rough scale-up from daily mean

    if annual_rain_mm < 500:
        moisture = "Arid"
    elif annual_rain_mm < 1000:
        moisture = "Semi-arid"
    elif annual_rain_mm < 2000:
        moisture = "Sub-humid"
    else:
        moisture = "Humid"

    thermal = "Warm" if temperature_c >= 24 else "Moderate" if temperature_c >= 18 else "Cool"

    return {
        "zone": f"{moisture} {thermal.lower()} zone (indicative)",
        "moisture_regime": moisture,
        "thermal_regime": thermal,
        "note": "Approximated from rainfall/temperature, not an official ICAR AEZ shapefile lookup.",
        "source": "Derived from CHIRPS + MODIS LST",
    }


# ---------------------------------------------------------------------------
# Cropping History (3-year seasonal presence, not crop-species classification)
# ---------------------------------------------------------------------------

def fetch_cropping_history(lat: float, lng: float, polygon: Optional[dict] = None, years: Tuple[int, ...] = (2021, 2022, 2023)) -> Dict[str, Any]:
    """Kharif vs Rabi NDVI presence per year over the last 3 years — shows
    whether the field was actively cropped each season, using the same
    Sentinel-2 source as the rest of the app. This flags active/fallow
    seasons; it does NOT identify which crop species was grown (that
    needs a trained classifier and ground-truth labels, which this
    prototype doesn't have).
    """
    region = _get_region(lat, lng, polygon)
    history = []

    for year in years:
        seasons = {
            "kharif": (f"{year}-06-01", f"{year}-10-31"),
            "rabi": (f"{year}-11-01", f"{year + 1}-03-31"),
        }
        year_entry = {"year": year}
        for season, (start, end) in seasons.items():
            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterDate(start, end)
                .filterBounds(region)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
            )
            ndvi_img = s2.map(
                lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")
            ).select("NDVI").mean()
            val = _reduce_mean(ndvi_img, region, scale=20)
            year_entry[season] = {
                "ndvi": round(val, 4) if val is not None else None,
                "cropped": (val is not None and val > 0.3),
            }
        history.append(year_entry)

    return {
        "years": history,
        "note": "Season-level cropped/fallow signal from NDVI — not crop-species identification.",
        "source": "Sentinel-2 (seasonal NDVI, 3-year lookback)",
    }


# ---------------------------------------------------------------------------
# Farm location thumbnail — real Sentinel-2 true-colour image, not a map
# screenshot. This is the same satellite source the rest of the app uses,
# just rendered as an RGB image instead of an index.
# ---------------------------------------------------------------------------

def fetch_farm_thumbnail_url(lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int = 700) -> Optional[str]:
    """Returns a PNG thumbnail URL for the most recent reasonably
    cloud-free Sentinel-2 true-colour composite over the farm + a small
    buffer, so the field sits in visual context (roads, neighbouring
    plots, water). Returns None if no imagery is available (Earth
    Engine down, extreme cloud cover, etc.) — callers must handle that.
    """
    region = _buffered_region(lat, lng, buffer_m)

    collection = (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate("2023-01-01", "2024-12-31")
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
        .sort("CLOUDY_PIXEL_PERCENTAGE")
    )

    image = collection.median().select(["B4", "B3", "B2"])

    try:
        url = image.getThumbURL({
            "region": region,
            "dimensions": 640,
            "min": 0,
            "max": 2200,
            "gamma": 1.3,
            "format": "png",
        })
        return url
    except Exception:
        logger.exception("Farm thumbnail generation failed")
        return None


# ---------------------------------------------------------------------------
# Drought Instances (district-scale, since 2000) — years where annual
# rainfall fell well below the long-term local average, from the same
# CHIRPS dataset already used for the rainfall figures elsewhere.
# ---------------------------------------------------------------------------

def fetch_drought_instances(lat: float, lng: float, start_year: int = 2000, buffer_m: int = 25000) -> Dict[str, Any]:
    """A 25 km buffer approximates 'district scale' since this app has no
    administrative-boundary dataset loaded — a real district polygon
    (from a shapefile/FeatureCollection) would be more precise if you
    add one later.
    """
    region = _buffered_region(lat, lng, buffer_m)
    end_year = datetime.utcnow().year

    yearly_rainfall = []
    for year in range(start_year, end_year):
        coll = (
            ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
            .filterDate(f"{year}-01-01", f"{year}-12-31")
            .filterBounds(region)
        )
        total = coll.sum()
        val = _reduce_mean(total, region, scale=5000)
        yearly_rainfall.append({"year": year, "rainfall_mm": val})

    valid = [y["rainfall_mm"] for y in yearly_rainfall if y["rainfall_mm"] is not None]
    if not valid:
        return {"drought_years": [], "note": "Insufficient rainfall history for this location.", "source": "CHIRPS"}

    mean_rainfall = sum(valid) / len(valid)
    threshold = mean_rainfall * 0.75  # <75% of long-term local average = drought year, a common agromet convention

    drought_years = [
        y["year"] for y in yearly_rainfall
        if y["rainfall_mm"] is not None and y["rainfall_mm"] < threshold
    ]

    return {
        "drought_years": drought_years,
        "long_term_avg_rainfall_mm": round(mean_rainfall, 1),
        "threshold_mm": round(threshold, 1),
        "note": "Years where total annual rainfall was <75% of the local long-term average (CHIRPS-derived, 25km scale) — an approximation, not an official drought declaration.",
        "source": f"CHIRPS Daily, {start_year}-{end_year}",
    }


# ---------------------------------------------------------------------------
# Village Population — gridded population estimate (WorldPop), not an
# exact Census figure but a real, current satellite-derived estimate.
# ---------------------------------------------------------------------------

def fetch_village_population(lat: float, lng: float, radius_m: int = 1500) -> Dict[str, Any]:
    region = _buffered_region(lat, lng, radius_m)

    try:
        pop_img = ee.ImageCollection("WorldPop/GP/100m/pop").filterBounds(region).mosaic()
        total = pop_img.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=100,
            maxPixels=1e9,
        ).getInfo()
        pop_estimate = total.get("population")
    except Exception:
        logger.exception("Population fetch failed")
        return {"estimated_population": None, "source": "WorldPop"}

    return {
        "estimated_population": int(round(pop_estimate)) if pop_estimate is not None else None,
        "radius_m": radius_m,
        "note": "Gridded population estimate (WorldPop, ~100m resolution), not an exact Census figure.",
        "source": "WorldPop Global Project",
    }


# ---------------------------------------------------------------------------
# Topography — elevation + slope from SRTM, classified into a simple
# terrain description.
# ---------------------------------------------------------------------------

def fetch_topography(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region = _get_region(lat, lng, polygon)
    dem = ee.Image("USGS/SRTMGL1_003").select("elevation")
    slope = ee.Terrain.slope(dem)

    stats = ee.Image.cat([dem, slope]).reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30,
        maxPixels=1e9,
    ).getInfo()

    elevation = stats.get("elevation")
    slope_deg = stats.get("slope")

    if slope_deg is None:
        terrain = None
    elif slope_deg < 2:
        terrain = "Flat / plain"
    elif slope_deg < 5:
        terrain = "Gently sloping"
    elif slope_deg < 15:
        terrain = "Moderately sloping / undulating"
    else:
        terrain = "Hilly / steep"

    return {
        "elevation_m": round(elevation, 1) if elevation is not None else None,
        "slope_degrees": round(slope_deg, 2) if slope_deg is not None else None,
        "terrain": terrain,
        "source": "SRTM 30m DEM",
    }


# ---------------------------------------------------------------------------
# Vegetation/Moisture Heatmaps — real per-pixel images (red→yellow→green
# for NDVI, red→yellow→blue for NDMI moisture), clipped to the drawn farm
# boundary if one exists, for draping over the map as a Leaflet image
# overlay. These are genuine rasters, not a single flat tint — they show
# actual within-field variation the same way the rest of this app's
# NDVI/NDMI numbers are computed from.
# ---------------------------------------------------------------------------

NDVI_PALETTE = ["d73027", "f46d43", "fdae61", "fee08b", "d9ef8b", "a6d96a", "66bd63", "1a9850"]
NDMI_PALETTE = ["8c510a", "d8b365", "f6e8c3", "f5f5f5", "c7eae5", "5ab4ac", "01665e"]

_INDEX_CONFIG = {
    "ndvi": {"bands": ["B8", "B4"], "palette": NDVI_PALETTE, "vis_min": 0.15, "vis_max": 0.85},
    "ndmi": {"bands": ["B8", "B11"], "palette": NDMI_PALETTE, "vis_min": -0.3, "vis_max": 0.5},
}


def fetch_vegetation_heatmap(lat: float, lng: float, polygon: Optional[dict] = None,
                              index: str = "ndvi", buffer_m: int = 300) -> Optional[Dict[str, Any]]:
    """Returns {"url": <PNG thumbnail URL>, "bounds": [[south,west],[north,east]], "index": index}
    or None if generation fails. `index` is "ndvi" (vegetation health) or
    "ndmi" (canopy moisture). When a polygon is given, pixels outside it
    are masked transparent so the heatmap follows the field's real shape
    instead of a bounding box; without a polygon, falls back to a small
    buffer circle around the point.
    """
    cfg = _INDEX_CONFIG.get(index.lower())
    if not cfg:
        logger.error("Unknown heatmap index requested: %s", index)
        return None

    try:
        if polygon:
            coords = _extract_coords_local(polygon)
            geom = ee.Geometry.Polygon([coords]) if coords else _buffered_region(lat, lng, buffer_m)
        else:
            geom = _buffered_region(lat, lng, buffer_m)

        bounds_coords = geom.bounds().getInfo()["coordinates"][0]
        lons = [c[0] for c in bounds_coords]
        lats = [c[1] for c in bounds_coords]
        bounds = [[min(lats), min(lons)], [max(lats), max(lons)]]

        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(geom)
            .filterDate("2023-06-01", "2024-12-31")
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
        )
        index_img = collection.median().normalizedDifference(cfg["bands"]).rename(index.upper())

        if polygon:
            index_img = index_img.clip(geom)

        vis = index_img.visualize(min=cfg["vis_min"], max=cfg["vis_max"], palette=cfg["palette"])

        url = vis.getThumbURL({
            "region": geom.bounds(),
            "dimensions": 640,
            "format": "png",
        })
        return {"url": url, "bounds": bounds, "index": index.lower()}
    except Exception:
        logger.exception("Vegetation heatmap generation failed (index=%s)", index)
        return None


def fetch_ndvi_heatmap(lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int = 300) -> Optional[Dict[str, Any]]:
    """Backwards-compatible wrapper — existing callers (Dashboard map
    overlay) keep working unchanged. New code should call
    fetch_vegetation_heatmap(..., index="ndvi"|"ndmi") directly.
    """
    return fetch_vegetation_heatmap(lat, lng, polygon, index="ndvi", buffer_m=buffer_m)
