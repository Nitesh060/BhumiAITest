from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean, initialise_earth_engine, S2_MAX_CLOUD_PCT

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


def _season_weather(region, start: str, end: str) -> Dict[str, Optional[float]]:
    rain = _reduce_mean(ee.ImageCollection(CHIRPS).filterDate(start, end).filterBounds(region).select("precipitation").mean(), region, 5566)
    temp_coll = ee.ImageCollection(MODIS).filterDate(start, end).filterBounds(region).select("LST_Day_1km")
    temp = _reduce_mean(temp_coll.mean().multiply(0.02).subtract(273.15), region, 1000)
    solar = _reduce_mean(ee.ImageCollection(ERA5).filterDate(start, end).filterBounds(region).select("surface_solar_radiation_downwards_sum").mean(), region, 10000)
    solar_mj = solar / 1_000_000 if solar is not None else None
    gdd_img = temp_coll.map(lambda img: img.multiply(0.02).subtract(273.15).subtract(10).max(0)).sum()
    gdd = _reduce_mean(gdd_img, region, 1000)
    return {"rainfall": rain, "lst": temp, "solar_radiation": solar_mj, "gdd": gdd}


def _season_spi(region, start: str, end: str, history_years: int = 5) -> Optional[float]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    duration = (e - s).days
    totals = []
    for i in range(history_years + 1):
        sy = date(s.year - i, s.month, s.day)
        ey = sy + timedelta(days=duration)
        val = _reduce_mean(ee.ImageCollection(CHIRPS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(region).sum(), region, 5566)
        if val is not None:
            totals.append(float(val))
    if len(totals) < 4:
        return None
    current, hist = totals[0], totals[1:]
    mean = sum(hist) / len(hist)
    sd = math.sqrt(sum((x - mean) ** 2 for x in hist) / len(hist))
    return round((current - mean) / sd, 2) if sd else None


def _season_spei(region, start: str, end: str, history_years: int = 5) -> Optional[float]:
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    duration = (e - s).days
    balances = []
    for i in range(history_years + 1):
        sy = date(s.year - i, s.month, s.day)
        ey = sy + timedelta(days=duration)
        rain = _reduce_mean(ee.ImageCollection(CHIRPS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(region).sum(), region, 5566)
        temp = _reduce_mean(ee.ImageCollection(MODIS).filterDate(sy.isoformat(), ey.isoformat()).filterBounds(region).select("LST_Day_1km").mean().multiply(0.02).subtract(273.15), region, 1000)
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
        optical = _season_optical(region, meta["start"], meta["end"])
        sar = _season_sar(region, meta["start"], meta["end"])
        weather = _season_weather(region, meta["start"], meta["end"])
        season_results[season] = {
            **meta, **optical, **sar, **weather,
            "spi": _season_spi(region, meta["start"], meta["end"]),
            "spei": _season_spei(region, meta["start"], meta["end"]),
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
