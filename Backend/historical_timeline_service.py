"""
historical_timeline_service.py
================================
Phase 2 — Historical Timeline + Before/After Comparison.

The rest of this app's NDVI trend (cropping_intensity's monthly_ndvi)
only covers ONE calendar year. This module extends that to a
multi-year view (2018-present, when Sentinel-2 has consistent global
coverage) and adds a two-date visual comparison tool.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import ee

from earth_engine_service import _get_region, _reduce_mean, _buffered_region

logger = logging.getLogger(__name__)

SENTINEL2_LAUNCH_YEAR = 2018  # global, consistently cloud-filterable coverage starts around here


def fetch_ndvi_historical_timeline(lat: float, lng: float, polygon: Optional[dict] = None,
                                     start_year: int = SENTINEL2_LAUNCH_YEAR, end_year: Optional[int] = None) -> Dict[str, Any]:
    """One NDVI value per quarter (Jan-Mar, Apr-Jun, Jul-Sep, Oct-Dec)
    from start_year to end_year — a long-run trend, not the detailed
    monthly view the single-year cropping-intensity chart already gives.
    """
    import datetime
    if end_year is None:
        end_year = datetime.datetime.utcnow().year

    region = _get_region(lat, lng, polygon)
    quarters = [("01-01", "03-31"), ("04-01", "06-30"), ("07-01", "09-30"), ("10-01", "12-31")]

    timeline: List[Dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        for qi, (start_md, end_md) in enumerate(quarters, start=1):
            start = f"{year}-{start_md}"
            end = f"{year}-{end_md}"
            s2 = (
                ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
                .filterDate(start, end)
                .filterBounds(region)
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 40))
            )
            ndvi_img = s2.map(
                lambda img: img.normalizedDifference(["B8", "B4"]).rename("NDVI")
            ).select("NDVI").mean()
            val = _reduce_mean(ndvi_img, region, scale=30)
            timeline.append({
                "year": year, "quarter": f"Q{qi}",
                "ndvi": round(val, 4) if val is not None else None,
            })

    return {
        "timeline": timeline,
        "start_year": start_year,
        "end_year": end_year,
        "note": "Quarterly NDVI average per period. Sparse/cloudy periods may show null.",
        "source": "Sentinel-2 (quarterly composites)",
    }


def fetch_before_after_comparison(lat: float, lng: float, date1: str, date2: str,
                                    polygon: Optional[dict] = None, buffer_m: int = 500) -> Dict[str, Any]:
    """Returns two true-colour thumbnail URLs — the closest reasonably
    cloud-free Sentinel-2 scene to each requested date — for a visual
    before/after comparison (e.g. pre- vs post-monsoon, pre- vs
    post-input-application).
    """
    region = _get_region(lat, lng, polygon) if polygon else _buffered_region(lat, lng, buffer_m)

    def _thumb_for_date(center_date: str) -> Optional[Dict[str, Any]]:
        import datetime
        d = datetime.datetime.strptime(center_date, "%Y-%m-%d")
        window_start = (d - datetime.timedelta(days=20)).strftime("%Y-%m-%d")
        window_end = (d + datetime.timedelta(days=20)).strftime("%Y-%m-%d")

        coll = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(region)
            .filterDate(window_start, window_end)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 30))
            .sort("CLOUDY_PIXEL_PERCENTAGE")
        )
        count = coll.size().getInfo()
        if count == 0:
            return None

        image = coll.first().select(["B4", "B3", "B2"])
        ndvi_img = coll.first().normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndvi_val = _reduce_mean(ndvi_img, region, scale=20)

        try:
            url = image.getThumbURL({"region": region, "dimensions": 500, "min": 0, "max": 2200, "gamma": 1.3, "format": "png"})
        except Exception:
            logger.exception("Before/after thumbnail generation failed")
            return None

        actual_date = ee.Date(coll.first().get("system:time_start")).format("YYYY-MM-dd").getInfo()
        return {"url": url, "actual_scene_date": actual_date, "ndvi": round(ndvi_val, 4) if ndvi_val is not None else None}

    before = _thumb_for_date(date1)
    after = _thumb_for_date(date2)

    return {
        "before": before,
        "after": after,
        "note": "Each image is the closest available cloud-free scene within ~20 days of the requested date, not the exact date.",
        "source": "Sentinel-2 true-colour",
    }
