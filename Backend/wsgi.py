"""Production WSGI wrapper for Bhumi AI."""

from __future__ import annotations

import importlib
import os
import sys
import time
import traceback
from collections import defaultdict, deque

try:
    app_module = importlib.import_module("app")
    app = app_module.app
except Exception:
    # Render's deploy log has repeatedly shown only:
    #   ImportError: cannot import name 'app' from 'app'
    # which is gunicorn's generic message for "the app module didn't
    # expose an `app` attribute" — it hides whatever ACTUALLY happened
    # inside app.py's own execution (a real exception partway through
    # its imports would normally show its own traceback instead of
    # this generic one, so something more specific is going on). Print
    # everything we can here so the next deploy log shows the real
    # cause instead of this dead end, then re-raise so the process
    # still fails exactly as before — this changes nothing except what
    # gets printed.
    print("=" * 70, file=sys.stderr)
    print("FATAL: failed to import the Flask `app` object from app.py.", file=sys.stderr)
    try:
        print(f"app module resolved to: {sys.modules['app'].__file__}", file=sys.stderr)
        print(f"attributes found on it: {sorted(n for n in dir(sys.modules['app']) if not n.startswith('_'))}", file=sys.stderr)
    except Exception:
        print("(could not introspect the partially-loaded 'app' module)", file=sys.stderr)
    print("Full traceback:", file=sys.stderr)
    traceback.print_exc()
    print("=" * 70, file=sys.stderr)
    raise

from alphaearth_service import register_alphaearth_routes
from seasonal_data_service import fetch_seasonal_comprehensive_data

register_alphaearth_routes(app)

# Existing 20-parameter FarmScore formula stays unchanged. Only the data
# windows are adapted to the latest available Kharif + Rabi seasons.
# No Base/Kharif/Rabi 200/400/400 split is introduced.
#
# Bounded in an OrderedDict so this cache can never grow without limit —
# on Render's free 512MB instance, an unbounded dict here (one entry per
# unique lat/lng/polygon ever queried) was a slow memory leak that added
# to the OOM risk during /calculate. Oldest entry is evicted once the
# cap is hit (simple LRU-ish behaviour, insertion order is good enough
# for our access pattern).
from collections import OrderedDict

_season_cache: "OrderedDict" = OrderedDict()
_SEASON_CACHE_MAX = 50
_original_fetch_farm_data = app_module.fetch_farm_data


def _season_key(lat, lng, polygon):
    return (round(float(lat), 5), round(float(lng), 5), str(polygon) if polygon else "")


def _get_seasonal(lat, lng, polygon=None):
    key = _season_key(lat, lng, polygon)
    if key not in _season_cache:
        _season_cache[key] = fetch_seasonal_comprehensive_data(lat, lng, polygon)
        if len(_season_cache) > _SEASON_CACHE_MAX:
            _season_cache.popitem(last=False)  # evict oldest entry
    else:
        _season_cache.move_to_end(key)  # keep recently-used entries alive longest
    return _season_cache[key]


def _seasonal_farm_data(lat, lng, polygon=None):
    base = _original_fetch_farm_data(lat, lng, polygon)
    seasonal = _get_seasonal(lat, lng, polygon)
    raw = seasonal["raw_values"]
    base.update({
        "ndvi": raw.get("ndvi"), "ndmi": raw.get("ndmi"), "ndwi": raw.get("ndwi"),
        "rainfall": raw.get("rainfall"), "temperature": raw.get("lst"),
        "seasonal_analysis": seasonal["seasons"],
        "season_method": seasonal["season_method"],
        "season_count_used": seasonal["season_count_used"],
    })
    return base


def _seasonal_extended_indices(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    return {
        "evi": raw.get("evi"), "evi_label": None,
        "savi": raw.get("savi"), "savi_label": None,
        "msavi": raw.get("msavi"), "bsi": None, "bsi_label": None,
        "ci_green": raw.get("ci_green"), "ci_rededge": raw.get("ci_rededge"),
        "ndwi": raw.get("ndwi"),
        "source": "Sentinel-2 SR Harmonized — latest Kharif + latest Rabi seasonal composite",
    }


def _seasonal_spectral(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    ndvi, ndre, ndmi = raw.get("ndvi") or 0.0, raw.get("ndre") or 0.0, raw.get("ndmi") or 0.0
    chlorophyll = max(0.0, min(100.0, ndvi * 100.0))
    nitrogen = max(0.0, min(100.0, (ndre + 0.1) / 0.6 * 100.0))
    msi = ((1 - ndmi) / (1 + ndmi)) if ndmi > -1 else 2.0
    moisture = max(0.0, min(100.0, (2.0 - msi) / 1.6 * 100.0))
    stress = max(0.0, min(100.0, 100.0 - abs(chlorophyll - moisture)))
    score = int(round(0.30 * chlorophyll + 0.25 * nitrogen + 0.25 * moisture + 0.20 * stress))
    grade = "Excellent" if score >= 85 else "Good" if score >= 70 else "Moderate" if score >= 50 else "Fair" if score >= 30 else "Poor"
    return {
        "spectral_score": score, "grade": grade,
        "method": "Seasonal Sentinel-2 multispectral proxy using latest Kharif and Rabi composites.",
        "indices": {
            "chlorophyll": {"label": "Chlorophyll & Canopy Health", "raw_value": round(ndvi, 4), "index": "NDVI", "sub_score": round(chlorophyll, 1), "weight": 30},
            "nitrogen": {"label": "Nitrogen / Red-Edge Health", "raw_value": round(ndre, 4), "index": "NDRE", "sub_score": round(nitrogen, 1), "weight": 25},
            "moisture_stress": {"label": "Moisture Status", "raw_value": round(ndmi, 4), "index": "NDMI", "sub_score": round(moisture, 1), "weight": 25},
            "stress_risk": {"label": "Signal Consistency / Stress Risk", "raw_value": round(stress, 1), "index": "Composite", "sub_score": round(stress, 1), "weight": 20},
        },
        "flags": [], "source": "Sentinel-2 SR Harmonized",
    }


def _seasonal_sar(lat, lng, polygon=None):
    raw = _get_seasonal(lat, lng, polygon)["raw_values"]
    vv, vh = raw.get("vv"), raw.get("vh")
    return {
        "available": vv is not None and vh is not None,
        "vv_db": vv, "vh_db": vh, "vh_vv_ratio": raw.get("vh_vv"), "rvi": raw.get("rvi"),
        "flood_signal": bool(vv is not None and vv < -17) if vv is not None else None,
        "source": "Sentinel-1 GRD — latest Kharif + latest Rabi seasonal composite",
    }


def _seasonal_solar(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("solar_radiation")
    return {"available": v is not None, "avg_daily_solar_radiation_mj_m2": v, "source": "ECMWF ERA5-Land Daily Aggregate"}


def _seasonal_spi(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("spi")
    return {"available": v is not None, "spi": v, "source": "CHIRPS — latest Kharif + latest Rabi seasonal history"}


def _seasonal_gdd(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("gdd")
    return {"available": v is not None, "gdd": v, "source": "MODIS LST"}


def _seasonal_spei(lat, lng, polygon=None):
    v = _get_seasonal(lat, lng, polygon)["raw_values"].get("spei")
    return {"available": v is not None, "spei_proxy": v, "source": "CHIRPS + MODIS LST"}


# Rebind the exact module-level names used by app.py's existing scoring code.
app_module.fetch_farm_data = _seasonal_farm_data
app_module.fetch_extended_indices = _seasonal_extended_indices
app_module.calculate_spectral_intelligence = _seasonal_spectral
app_module._fetch_sar_for_flood = _seasonal_sar
app_module.fetch_solar_radiation = _seasonal_solar
app_module.fetch_spi = _seasonal_spi
app_module.fetch_gdd = _seasonal_gdd
app_module.fetch_spei_proxy = _seasonal_spei


MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
RATE_WINDOW_SECONDS = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "").strip()
EXPENSIVE_PATHS = {
    "/calculate", "/comprehensive-score", "/diagnose", "/spectral", "/spectral-indices", "/sar-moisture",
    "/historical-timeline", "/before-after", "/vegetation-heatmap", "/ndvi-heatmap", "/crop-intelligence",
    "/farm-advisor", "/risk-analysis",
}
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(environ):
    return environ.get("REMOTE_ADDR", "unknown")


def _rate_limited(environ):
    path = environ.get("PATH_INFO", "")
    if path not in EXPENSIVE_PATHS or environ.get("REQUEST_METHOD") != "POST":
        return False
    now = time.time(); key = f"{_client_ip(environ)}:{path}"; bucket = _hits[key]; cutoff = now - RATE_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return True
    bucket.append(now)
    return False


def _response(start_response, status, body, headers=None):
    data = body.encode("utf-8")
    base = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))]
    if headers: base.extend(headers)
    start_response(status, base)
    return [data]


def middleware(environ, start_response):
    length = environ.get("CONTENT_LENGTH")
    try:
        if length and int(length) > MAX_REQUEST_BYTES:
            return _response(start_response, "413 Payload Too Large", '{"error":"Request too large"}')
    except ValueError:
        return _response(start_response, "400 Bad Request", '{"error":"Invalid Content-Length"}')
    if _rate_limited(environ):
        return _response(start_response, "429 Too Many Requests", '{"error":"Rate limit exceeded. Please wait before retrying."}', [("Retry-After", str(RATE_WINDOW_SECONDS))])

    def secured_start_response(status, headers, exc_info=None):
        filtered = [(k, v) for k, v in headers if k.lower() not in {"server", "access-control-allow-origin", "access-control-allow-credentials"}]
        origin = environ.get("HTTP_ORIGIN")
        if ALLOWED_ORIGIN and origin == ALLOWED_ORIGIN:
            filtered.append(("Access-Control-Allow-Origin", ALLOWED_ORIGIN)); filtered.append(("Vary", "Origin"))
        filtered.extend([
            ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
        ])
        return start_response(status, filtered, exc_info)

    return app(environ, secured_start_response)


application = middleware
