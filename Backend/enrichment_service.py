"""Additional satellite-data enrichment helpers for FarmScore."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import ee

from earth_engine_service import _get_region, _reduce_mean, _buffered_region

logger = logging.getLogger(__name__)

_USDA_TEXTURE_LABELS = {1:"Clay",2:"Silty Clay",3:"Sandy Clay",4:"Clay Loam",5:"Silty Clay Loam",6:"Sandy Clay Loam",7:"Loam",8:"Silty Loam",9:"Sandy Loam",10:"Silt",11:"Loamy Sand",12:"Sand"}
_WORLDCOVER_LABELS = {10:"Tree cover",20:"Shrubland",30:"Grassland",40:"Cropland",50:"Built-up",60:"Bare / sparse vegetation",70:"Snow and ice",80:"Permanent water bodies",90:"Herbaceous wetland",95:"Mangroves",100:"Moss and lichen"}


def fetch_soil_type(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region = _get_region(lat, lng, polygon)
    result = ee.Image("OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02").select("b0").reduceRegion(reducer=ee.Reducer.mode(), geometry=region, scale=250, maxPixels=1e9).getInfo() or {}
    code = result.get("b0")
    return {"class_code": int(round(code)) if code is not None else None, "label": _USDA_TEXTURE_LABELS.get(int(round(code)), "Unknown") if code is not None else None, "source": "OpenLandMap SoilGrids (0 cm depth)"}


def fetch_adjacent_land_cover(lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int = 1000) -> Dict[str, Any]:
    farm_region = _get_region(lat, lng, polygon)
    outer = farm_region.buffer(buffer_m) if polygon else _buffered_region(lat, lng, buffer_m)
    ring = outer.difference(farm_region, ee.ErrorMargin(10))
    hist = ee.ImageCollection("ESA/WorldCover/v200").first().select("Map").reduceRegion(reducer=ee.Reducer.frequencyHistogram(), geometry=ring, scale=10, maxPixels=1e9, bestEffort=True).getInfo() or {}
    counts = hist.get("Map", {})
    total = sum(counts.values()) or 1
    breakdown = [{"class": _WORLDCOVER_LABELS.get(int(float(k)), "Unknown"), "percent": round(100*v/total,1)} for k,v in counts.items()]
    breakdown.sort(key=lambda x:x["percent"], reverse=True)
    return {"buffer_m":buffer_m,"breakdown":breakdown,"source":"ESA WorldCover v200 (10 m)"}


def _monthly_ndvi_feature(m, s2_all, region):
    m = ee.Number(m)
    start = ee.Date.fromYMD(2023, m, 1)
    end = start.advance(1, "month")
    s2 = s2_all.filterDate(start, end).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
    ndvi = s2.map(lambda img: img.normalizedDifference(["B8","B4"]).rename("NDVI")).select("NDVI").mean()
    safe_img = ee.Image(ee.Algorithms.If(s2.size().gt(0), ndvi, ee.Image.constant(-999).rename("NDVI")))
    val = safe_img.reduceRegion(reducer=ee.Reducer.mean(), geometry=region, scale=20, maxPixels=1e9).get("NDVI")
    return ee.Feature(None, {"month":m, "ndvi":val})


def fetch_cropping_intensity(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region = _get_region(lat, lng, polygon)
    s2_all = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
    try:
        fc = ee.FeatureCollection(ee.List.sequence(1,12).map(lambda m: _monthly_ndvi_feature(m,s2_all,region)))
        raw = fc.getInfo() or {}
        features = sorted(raw.get("features",[]), key=lambda f:f["properties"]["month"])
        monthly = []
        for f in features:
            v = f.get("properties",{}).get("ndvi")
            monthly.append(None if v is None or v <= -900 else round(float(v),4))
    except Exception:
        logger.exception("Batched cropping-intensity fetch failed")
        monthly = [None]*12
    peaks = 0
    if len([v for v in monthly if v is not None]) >= 3:
        for i in range(1,11):
            if monthly[i] is not None and monthly[i-1] is not None and monthly[i+1] is not None and monthly[i] > monthly[i-1]+0.08 and monthly[i] > monthly[i+1]+0.08:
                peaks += 1
    peaks = max(1,peaks)
    return {"monthly_ndvi":monthly,"estimated_cycles":peaks,"label":{1:"Single cropping (mono)",2:"Double cropping"}.get(peaks,"Triple / multi cropping"),"note":"Estimated from NDVI seasonality, not ground-truth crop calendar data.","source":"Sentinel-2 (12-month NDVI series)"}


def fetch_irrigation_signal(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region = _get_region(lat,lng,polygon)
    coll = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterDate("2024-02-01","2024-04-30").filterBounds(region).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE",40))
    try:
        ndvi = coll.map(lambda img:img.normalizedDifference(["B8","B4"]).rename("NDVI")).select("NDVI").mean()
        val = _reduce_mean(ndvi,region,scale=20)
    except Exception:
        val = None
    return {"dry_season_ndvi":round(val,4) if val is not None else None,"likely_irrigated":val>0.35 if val is not None else None,"confidence":"Indicative — based on dry-season vegetation greenness, not canal/pump records.","source":"Sentinel-2 (Feb-Apr NDVI)"}


def fetch_temperature_annual_range(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region=_get_region(lat,lng,polygon)
    coll=ee.ImageCollection("MODIS/061/MOD11A1").filterDate("2023-01-01","2024-01-01").filterBounds(region).select("LST_Day_1km").map(lambda img:img.multiply(0.02).subtract(273.15).rename("LST_C"))
    try:
        stats=coll.reduce(ee.Reducer.minMax().combine(ee.Reducer.mean(),sharedInputs=True)).reduceRegion(reducer=ee.Reducer.mean(),geometry=region,scale=1000,maxPixels=1e9).getInfo() or {}
    except Exception:
        stats={}
    return {"min_c":round(stats["LST_C_min"],2) if stats.get("LST_C_min") is not None else None,"max_c":round(stats["LST_C_max"],2) if stats.get("LST_C_max") is not None else None,"mean_c":round(stats["LST_C_mean"],2) if stats.get("LST_C_mean") is not None else None,"source":"MODIS LST (full calendar year 2023)"}


def fetch_prosperity_proxy(lat: float, lng: float, polygon: Optional[dict] = None, radius_m: int = 5000) -> Dict[str, Any]:
    region=_buffered_region(lat,lng,radius_m)
    try: val=_reduce_mean(ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG").filterDate("2023-01-01","2024-01-01").select("avg_rad").mean(),region,scale=500)
    except Exception: val=None
    tier=None if val is None else ("Low economic activity (rural/agrarian)" if val<1 else "Moderate economic activity" if val<5 else "High economic activity (peri-urban/urban proximity)")
    return {"avg_radiance":round(val,3) if val is not None else None,"tier":tier,"note":"Proxy indicator from satellite nightlights, not an official government prosperity index.","source":"VIIRS Nighttime Lights, 5 km radius"}


def fetch_nearest_water_body_signal(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region=_buffered_region(lat,lng,2000)
    try: result=ee.Image("JRC/GSW1_4/GlobalSurfaceWater").select("occurrence").gt(50).selfMask().reduceRegion(reducer=ee.Reducer.count(),geometry=region,scale=30,maxPixels=1e9).getInfo() or {}
    except Exception: result={}
    px=int(result.get("occurrence",0) or 0)
    return {"water_pixels_within_2km":px,"water_present":px>0,"source":"JRC Global Surface Water (30 m, >50% occurrence)"}


def estimate_agro_ecological_zone(rainfall_mm_day: Optional[float], temperature_c: Optional[float]) -> Dict[str, Any]:
    if rainfall_mm_day is None or temperature_c is None: return {"zone":None,"note":"Insufficient data"}
    annual=rainfall_mm_day*365
    moisture="Arid" if annual<500 else "Semi-arid" if annual<1000 else "Sub-humid" if annual<2000 else "Humid"
    thermal="Cool" if temperature_c<18 else "Warm" if temperature_c<28 else "Hot"
    return {"zone":f"{moisture} / {thermal}","note":"Indicative rule-based AEZ-style classification, not the official ICAR/NBSS&LUP zone lookup."}


def _season_feature(y, season, s2_all, region):
    y=ee.Number(y)
    if season=="kharif": start=ee.Date.fromYMD(y,6,1); end=ee.Date.fromYMD(y,11,1)
    else: start=ee.Date.fromYMD(y,11,1); end=ee.Date.fromYMD(y.add(1),3,1)
    s2=s2_all.filterDate(start,end).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE",40))
    ndvi=s2.map(lambda img:img.normalizedDifference(["B8","B4"]).rename("NDVI")).select("NDVI").mean()
    safe=ee.Image(ee.Algorithms.If(s2.size().gt(0),ndvi,ee.Image.constant(-999).rename("NDVI")))
    val=safe.reduceRegion(reducer=ee.Reducer.mean(),geometry=region,scale=20,maxPixels=1e9).get("NDVI")
    return ee.Feature(None,{"year":y,"ndvi":val})


def fetch_cropping_history(lat: float, lng: float, polygon: Optional[dict] = None, years: Tuple[int,...]=(2021,2022,2023)) -> Dict[str, Any]:
    region=_get_region(lat,lng,polygon); s2=ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region)
    out=[]
    try:
        for season in ("kharif","rabi"):
            fc=ee.FeatureCollection(ee.List(years).map(lambda y:_season_feature(y,season,s2,region)))
            raw=fc.getInfo() or {}
            for f in raw.get("features",[]):
                v=f.get("properties",{}).get("ndvi")
                out.append({"year":int(f["properties"]["year"]),"season":season,"ndvi":None if v is None or v<=-900 else round(float(v),4),"active":bool(v is not None and v>-900 and v>0.25)})
    except Exception:
        logger.exception("Cropping-history fetch failed")
    return {"history":sorted(out,key=lambda x:(x["year"],x["season"])),"source":"Sentinel-2 seasonal NDVI"}


def fetch_drought_instances(lat: float, lng: float, start_year: int=2000, buffer_m: int=25000) -> Dict[str, Any]:
    region=_buffered_region(lat,lng,buffer_m)
    chirps=ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
    years=list(range(start_year,datetime.utcnow().year))
    try:
        vals=[]
        for y in years:
            img=chirps.filterDate(f"{y}-01-01",f"{y+1}-01-01").filterBounds(region).sum()
            v=img.reduceRegion(reducer=ee.Reducer.mean(),geometry=region,scale=5566,maxPixels=1e9,bestEffort=True).get("precipitation")
            vals.append(ee.Feature(None,{"year":y,"rainfall":v}))
        raw=ee.FeatureCollection(vals).getInfo() or {}
        pairs=[(f["properties"]["year"],f["properties"].get("rainfall")) for f in raw.get("features",[]) if f["properties"].get("rainfall") is not None]
        if not pairs:return {"drought_years":[],"count":0,"source":"CHIRPS v3"}
        mean=sum(v for _,v in pairs)/len(pairs); threshold=0.7*mean
        drought=[y for y,v in pairs if v<threshold]
        return {"drought_years":drought,"count":len(drought),"threshold_mm":round(threshold,1),"long_term_mean_mm":round(mean,1),"source":"CHIRPS v3, 25 km local buffer"}
    except Exception:
        logger.exception("Drought-instance fetch failed")
        return {"drought_years":[],"count":0,"source":"CHIRPS v3"}


def fetch_village_population(lat: float, lng: float, radius_m: int=1500) -> Dict[str, Any]:
    region=_buffered_region(lat,lng,radius_m)
    try:
        img=ee.ImageCollection("WorldPop/GP/100m/pop").filterBounds(region).mosaic()
        result=img.reduceRegion(reducer=ee.Reducer.sum(),geometry=region,scale=100,maxPixels=1e9,bestEffort=True).getInfo() or {}
        total=next(iter(result.values()),None)
    except Exception: total=None
    return {"estimated_population":round(float(total)) if total is not None else None,"radius_m":radius_m,"source":"WorldPop 100m gridded population estimate"}


def fetch_topography(lat: float, lng: float, polygon: Optional[dict] = None) -> Dict[str, Any]:
    region=_get_region(lat,lng,polygon); dem=ee.Image("USGS/SRTMGL1_003").select("elevation"); slope=ee.Terrain.slope(dem)
    try: stats=ee.Image.cat([dem.rename("elevation"),slope.rename("slope")]).reduceRegion(reducer=ee.Reducer.mean(),geometry=region,scale=30,maxPixels=1e9).getInfo() or {}
    except Exception: stats={}
    elev=stats.get("elevation"); sl=stats.get("slope")
    terrain="Flat" if sl is not None and sl<3 else "Gently sloping" if sl is not None and sl<8 else "Sloping" if sl is not None and sl<15 else "Steep" if sl is not None else None
    return {"elevation_m":round(elev,1) if elev is not None else None,"slope_degrees":round(sl,2) if sl is not None else None,"terrain":terrain,"source":"SRTM 30 m"}


def fetch_farm_thumbnail_url(lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int=700) -> Optional[str]:
    region=_get_region(lat,lng,polygon).buffer(buffer_m)
    try:
        img=ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filterDate("2025-01-01",datetime.utcnow().strftime("%Y-%m-%d")).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE",30)).sort("CLOUDY_PIXEL_PERCENTAGE").first()
        if img is None:return None
        return img.select(["B4","B3","B2"]).visualize(min=0,max=3000).getThumbURL({"region":region,"dimensions":512,"format":"png"})
    except Exception:
        logger.exception("Farm thumbnail generation failed")
        return None


def fetch_vegetation_heatmap(lat: float, lng: float, polygon: Optional[dict] = None, index: str="ndvi", buffer_m: int=300) -> Optional[Dict[str, Any]]:
    if index not in ("ndvi","ndmi"): index="ndvi"
    region=_get_region(lat,lng,polygon).buffer(buffer_m)
    try:
        coll=ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(region).filterDate("2025-01-01",datetime.utcnow().strftime("%Y-%m-%d")).filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE",40))
        img=coll.median()
        if index=="ndmi": band_img=img.normalizedDifference(["B8","B11"])
        else: band_img=img.normalizedDifference(["B8","B4"])
        vis={"min":-0.3 if index=="ndmi" else 0.15,"max":0.5 if index=="ndmi" else 0.85,"palette":["red","yellow","green"]}
        url=band_img.visualize(**vis).getThumbURL({"region":region,"dimensions":512,"format":"png"})
        return {"url":url,"index":index,"source":"Sentinel-2"}
    except Exception:
        logger.exception("Vegetation heatmap generation failed")
        return None


def fetch_ndvi_heatmap(lat: float, lng: float, polygon: Optional[dict] = None, buffer_m: int=300) -> Optional[Dict[str, Any]]:
    return fetch_vegetation_heatmap(lat,lng,polygon,index="ndvi",buffer_m=buffer_m)
