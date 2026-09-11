"""Reverse-geocode a farm centroid into Indian administrative fields.

The score response used to carry only raw lat/lng, so every
State / District / Tehsil / Village column in a side-by-side comparison
against a land-record report came out blank. This fills those in from the
coordinates.

Scope note — what this CANNOT do: Survey Number, Plot Number and Khatiyan
Number are land-record (RoR) identifiers held by state revenue departments.
They are not derivable from coordinates by any satellite or geocoding
source, so they are returned as None with an explicit reason rather than
guessed at. Wire a state RoR/Bhulekh API into `fetch_land_record()` below
to populate them.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/reverse")
USER_AGENT = os.environ.get("NOMINATIM_USER_AGENT", "BhumiAI/1.0 (farm scoring)")
REQUEST_TIMEOUT = float(os.environ.get("NOMINATIM_TIMEOUT", "8"))

# Nominatim's usage policy allows at most 1 request/second from one client.
_rate_lock = threading.Lock()
_last_call_at = 0.0

_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _throttle() -> None:
    global _last_call_at
    with _rate_lock:
        wait = 1.0 - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _pick(address: Dict[str, Any], *keys: str) -> Optional[str]:
    """First non-empty value among `keys`. Nominatim is inconsistent about
    which key holds a given administrative level across Indian states, so
    each field has to try several."""
    for key in keys:
        value = address.get(key)
        if value:
            return str(value).strip()
    return None


def fetch_location(lat: float, lng: float) -> Dict[str, Any]:
    """Administrative fields for a coordinate. Fails soft — on any error it
    returns the same shape with None values and a `reason`, so a geocoding
    outage degrades the report instead of breaking the score."""
    cache_key = f"{round(lat, 5)},{round(lng, 5)}"
    with _cache_lock:
        if cache_key in _cache:
            return _cache[cache_key]

    result: Dict[str, Any] = {
        "state": None, "district": None, "tehsil": None, "village": None,
        "postcode": None, "country": None,
        "centroid": f"{abs(lat):.6f}°{'N' if lat >= 0 else 'S'} {abs(lng):.6f}°{'E' if lng >= 0 else 'W'}",
        "source": "OpenStreetMap Nominatim",
        "reason": None,
    }

    try:
        _throttle()
        response = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lng, "format": "jsonv2", "zoom": 14, "addressdetails": 1},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        address = (response.json() or {}).get("address") or {}

        result["state"] = _pick(address, "state")
        result["district"] = _pick(address, "state_district", "district", "county")
        result["tehsil"] = _pick(address, "county", "subdistrict", "city_district", "municipality")
        result["village"] = _pick(address, "village", "hamlet", "town", "suburb", "city")
        result["postcode"] = _pick(address, "postcode")
        result["country"] = _pick(address, "country")

        # District and tehsil both fall back to `county`; if that happened,
        # don't report the same name twice as if they were two findings.
        if result["tehsil"] and result["tehsil"] == result["district"]:
            result["tehsil"] = None

        if not any((result["state"], result["district"], result["village"])):
            result["reason"] = "No administrative boundary matched these coordinates."
    except Exception as exc:
        logger.warning("Reverse geocoding failed for %.5f,%.5f: %s", lat, lng, exc)
        result["reason"] = f"Reverse geocoding unavailable ({type(exc).__name__})."

    with _cache_lock:
        _cache[cache_key] = result
    return result


def fetch_land_record(lat: float, lng: float) -> Dict[str, Any]:
    """Survey / Plot / Khatiyan numbers.

    Deliberately unimplemented. These come from state Records-of-Rights
    systems (Odisha Bhulekh, Maharashtra 7/12, etc.), each with its own API,
    auth and schema — there is no satellite or geocoding route to them. The
    explicit `reason` is so the report shows *why* the field is blank rather
    than a bare "NA" that looks like a bug.
    """
    return {
        "survey_no": None,
        "plot_no": None,
        "khatiyan_no": None,
        "available": False,
        "reason": (
            "Survey/Plot/Khatiyan numbers are state land-record identifiers and cannot be "
            "derived from coordinates. Connect a state RoR/Bhulekh API to populate them."
        ),
    }
