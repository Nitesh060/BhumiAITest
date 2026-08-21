from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import ee

from earth_engine_service import (
    _get_region, _reduce_mean_with_retry, _scaled_region,
    initialise_earth_engine, S2_MAX_CLOUD_PCT,
    CHIRPS_SCALE_M, MODIS_LST_SCALE_M, ERA5_LAND_SCALE_M,
)

S2 = "COPERNICUS/S2_SR_HARMONIZED"
S1 = "COPERNICUS/S1_GRD"
CHIRPS = "UCSB-CHG/CHIRPS/DAILY"
MODIS = "MODIS/061/MOD11A1"
ERA5 = "ECMWF/ERA5_LAND/DAILY_AGGR"


def latest_season_windows(today: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    today = today or date.today()
    y = today.year
    # Kharif: use the current season when it has started; otherwise latest completed season.
    if today >= date(y, 6, 1):
        k_year = y
        k_end = min(today + timedelta(days=1), date(y, 11, 1))
    else:
        k_year = y - 1
        k_end = date(y, 11, 1)
    k_start = date(k_year, 6, 1)

    # Rabi: use the latest completed season when available; otherwise latest available Rabi-to-date.
    if today >= date(y, 5, 1) and today < date(y, 11, 1):
        r_year = y - 1
        r_end = date(y, 5, 1)
    elif today >= date(y, 11, 1):
        r_year = y
        r_end = date(y + 1, 5, 1)
    else:
        r_year = y - 2
        r_end = date(y - 1, 5, 1)
    r_start = date(r_year, 11, 1)

    return {
        "kharif": {"label": f"Kharif {k_year}", "start": k_start.isoformat(), "end": k_end.isoformat(), "complete": k_end >= date(k_year, 11, 1)},
        "rabi": {"label": f"Rabi {r_year}-{str(r_year + 1)[-2:]}", "start": r_start.isoformat(), "end": r_end.isoformat(), "complete": r_end >= date(r_year + 1, 5, 1)},
    }


def _season_optical(region, start: str, end: str) -> Dict[str, Optional[float]]:
    coll = (ee.ImageCollection(S2).filterDate(start, end).filterBounds(region)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", S2_MAX_CLOUD_PCT)))

    def calc(img):
        nir = img.select("B8").divide(10000)
        red = img.select("B4").divide(10000)
        blue = img.select("B2").divide(10000)
        green = img.select("B3").divide(10000)
        swir = img.select("B11").divide(10000)
        re = img.select("B5").divide(10000)
        ndvi = nir.subtract(red).divide(nir.add(red)).rename("ndvi")
        ndmi = nir.subtract(swir).divide(nir.add(swir)).rename("ndmi")
        ndwi = green.subtract(nir).divide(green.add(nir)).rename("ndwi")
        evi = nir.subtract(red).multiply(2.5).divide(nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)).rename("evi")
        savi = nir.subtract(red).divide(nir.add(red).add(0.5)).multiply(1.5).rename("savi")
        msavi = nir.multiply(2).add(1).subtract(nir.multiply(2).add(1).pow(2).subtract(nir.subtract(red).multiply(8)).sqrt()).divide(2).rename("msavi")
        ndre = nir.subtract(re).divide(nir.add(re)).rename("ndre")
        ci_green = nir.divide(green).subtract(1).rename("ci_green")
        ci_rededge = nir.divide(re).subtract(1).rename("ci_rededge")
        return ee.Image.cat([ndvi, evi, savi, msavi, ndre, ndmi, ndwi, ci_green, ci_rededge])

    comp = coll.map(calc).mean()
    out = comp.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=10, maxPixels=1e9).getInfo()
    return {k: (float(out.get(k)) if out and out.get(k) is not None else None) for k in ["ndvi", "evi", "savi", "msavi", "ndre", "ndmi", "ndwi", "ci_green", "ci_rededge"]}


def _season_sar(region, start: str, end: str) -> Dict[str, Optional[float]]:
    coll = (ee.ImageCollection(S1).filterBounds(region).filterDate(start, end)
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            .select(["VV", "VH"]))
    img = coll.median()
    out = img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9).getInfo()
    vv, vh = out.get("VV"), out.get("VH")
    if vv is None or vh is None:
        return {"vv": None, "vh": None, "vh_vv": None, "rvi": None}
    vv, vh = float(vv), float(vh)
    vv_lin, vh_lin = 10 ** (vv / 10), 10 ** (vh / 10)
    return {"vv": vv, "vh": vh, "vh_vv": vh_lin / vv_lin if vv_lin else None, "rvi": 4 * vh_lin / (vv_lin + vh_lin) if (vv_lin + vh_lin) else None}


def _season_weather(lat: float, lng: float, polygon: Optional[dict], start: str, end: str) -> Dict[str, Optional[float]]:
    """Root cause of Rainfall/Solar Radiation always showing "no data" on
    the live dashboard: this used to reduce against the bare `region`
    from `_get_region()` — a ~30m point buffer (or a small farm
    polygon) — regardless of the dataset's own pixel size. Earth
    Engine's weighted `ee.Reducer.mean()` only counts a pixel if the
    reduceRegion() geometry covers at least ~0.4% of that pixel's area
    (see earth_engine_service._min_scale_buffer_m's docstring). CHIRPS's
    pixel is ~5.5km and ERA5-Land's is ~11km, so a 30m buffer covers a
    tiny fraction of a percent of either — rainfall and solar radiation
    reduced to None on essentially every request. MODIS's ~1km pixel
    (temperature/GDD) just barely cleared that floor, which is why GDD
    and LST kept working while rainfall/solar didn't, even though all
    four came from the same function. Switched to `_scaled_region` /
    `_reduce_mean_with_retry` — the exact fix already proven for these
    same three datasets in weather_indices_service.py — so every
    reduction gets a region sized to its own dataset's pixel scale
    instead of one sized for Sentinel-1/2's much finer 10-20m pixels.
    """
    chirps_region = _scaled_region(lat, lng, polygon, CHIRPS_SCALE_M)
    modis_region = _scaled_region(lat, lng, polygon, MODIS_LST_SCALE_M)
    era5_region = _scaled_region(lat, lng, polygon, ERA5_LAND_SCALE_M)

    rain_img = ee.ImageCollection(CHIRPS).filterDate(start, end).filterBounds(chirps_region).select("precipitation").mean()
    rain = _reduce_mean_with_retry(rain_img, lat, lng, polygon, CHIRPS_SCALE_M)

    temp_coll = ee.ImageCollection(MODIS).filterDate(start, end).filterBounds(modis_region).select("LST_Day_1km")
    temp = _reduce_mean_with_retry(temp_coll.mean().multiply(0.02).subtract(273.15), lat, lng, polygon, MODIS_LST_SCALE_M)

    solar_img = ee.ImageCollection(ERA5).filterDate(start, end).filterBounds(era5_region).select("surface_solar_radiation_downwards_sum").mean()
    solar = _reduce_mean_with_retry(solar_img, lat, lng, polygon, ERA5_LAND_SCALE_M)
    solar_mj = solar / 1_000_000 if solar is not None else None

    gdd_img = temp_coll.map(lambda img: img.multiply(0.02).subtract(273.15).subtract(10).max(0)).sum()
    gdd = _reduce_mean_with_retry(gdd_img, lat, lng, polygon, MODIS_LST_SCALE_M)
    return {"rainfall": rain, "lst": temp, "solar_radiation": solar_mj, "gdd": gdd}


def _season_spi(lat: float, lng: float, polygon: Optional[dict], start: str, end: str, history_years: int = 5) -> Optional[float]:
    """Same _get_region/_reduce_mean pixel-coverage bug as _season_weather
    (CHIRPS's ~5.5km pixel vs a ~30m region) — switched to
    _scaled_region/_reduce_mean_with_retry for the same reason."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    duration = (e - s).days
    chirps_region = _scaled_region(lat, lng, polygon, CHIRPS_SCALE_M)
    totals = []
    for i in range(history_years + 1):
        sy = date(s.year - i, s.month, s.day)
        ey = sy + timedelta(days=duration)
        img = ee.ImageCollection(CHIRPS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(chirps_region).sum()
        val = _reduce_mean_with_retry(img, lat, lng, polygon, CHIRPS_SCALE_M)
        if val is not None:
            totals.append(float(val))
    if len(totals) < 4:
        return None
    current, hist = totals[0], totals[1:]
    mean = sum(hist) / len(hist)
    sd = math.sqrt(sum((x - mean) ** 2 for x in hist) / len(hist))
    return round((current - mean) / sd, 2) if sd else None


def _season_spei(lat: float, lng: float, polygon: Optional[dict], start: str, end: str, history_years: int = 5) -> Optional[float]:
    """Same pixel-coverage bug as _season_weather/_season_spi for the
    CHIRPS component (the MODIS component's ~1km pixel already cleared
    the coverage floor even with the old unscaled region, same as GDD/LST)."""
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    duration = (e - s).days
    chirps_region = _scaled_region(lat, lng, polygon, CHIRPS_SCALE_M)
    modis_region = _scaled_region(lat, lng, polygon, MODIS_LST_SCALE_M)
    balances = []
    for i in range(history_years + 1):
        sy = date(s.year - i, s.month, s.day)
        ey = sy + timedelta(days=duration)
        rain_img = ee.ImageCollection(CHIRPS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(chirps_region).sum()
        rain = _reduce_mean_with_retry(rain_img, lat, lng, polygon, CHIRPS_SCALE_M)
        temp_img = ee.ImageCollection(MODIS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(modis_region).select("LST_Day_1km").mean().multiply(0.02).subtract(273.15)
        temp = _reduce_mean_with_retry(temp_img, lat, lng, polygon, MODIS_LST_SCALE_M)
        if rain is None or temp is None:
            continue
        t = max(float(temp), 0.1)
        heat = (t / 5) ** 1.514
        a = 6.75e-7 * heat ** 3 - 7.71e-5 * heat ** 2 + 1.792e-2 * heat + 0.49239
        pet = 16 * ((10 * t / heat) ** a) * max(duration / 30.0, 1) if heat > 0 else 0
        balances.append(float(rain) - pet)
    if len(balances) < 4:
        return None
    current, hist = balances[0], balances[1:]
    mean = sum(hist) / len(hist)
    sd = math.sqrt(sum((x - mean) ** 2 for x in hist) / len(hist))
    return round((current - mean) / sd, 2) if sd else None


def fetch_seasonal_comprehensive_data(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    """Fetch the existing 20 FarmScore parameters for the latest Kharif and Rabi windows.

    The two seasonal parameter vectors are averaged equally for the existing 300–900
    FarmScore. No Base/Kharif/Rabi 200/400/400 split is introduced here.
    """
    initialise_earth_engine()
    region = _get_region(lat, lng, polygon)
    windows = latest_season_windows()
    season_results = {}
    for season, meta in windows.items():
        # Sentinel-1/2 (10-20m native pixels) stay on the plain `region` —
        # even a small point buffer covers plenty of pixels at that
        # resolution, which is exactly why these never showed the
        # no-data bug the CHIRPS/ERA5/MODIS-based fields below did.
        optical = _season_optical(region, meta["start"], meta["end"])
        sar = _season_sar(region, meta["start"], meta["end"])
        weather = _season_weather(lat, lng, polygon, meta["start"], meta["end"])
        season_results[season] = {
            **meta, **optical, **sar, **weather,
            "spi": _season_spi(lat, lng, polygon, meta["start"], meta["end"]),
            "spei": _season_spei(lat, lng, polygon, meta["start"], meta["end"]),
        }

    keys = ["ndvi", "evi", "savi", "msavi", "ndre", "ndmi", "ndwi", "ci_green", "ci_rededge",
            "vv", "vh", "vh_vv", "rvi", "rainfall", "solar_radiation", "spi", "spei", "gdd", "lst"]
    combined = {}
    for key in keys:
        vals = [r[key] for r in season_results.values() if r.get(key) is not None]
        combined[key] = round(sum(vals) / len(vals), 6) if vals else None

    return {
        "raw_values": combined,
        "seasons": season_results,
        "season_method": "Latest available Kharif + latest available Rabi, equally weighted into the existing 20-parameter score.",
        "season_count_used": len(season_results),
    }
