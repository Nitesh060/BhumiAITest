"""Production WSGI wrapper for Bhumi AI."""

from __future__ import annotations

import importlib
import io
import json
import os
import sys
import time
import traceback
from collections import defaultdict, deque

try:
    app_module = importlib.import_module("app")
    app = app_module.app
except Exception:
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
from land_verification_service import register_land_verification_routes, verify_token

register_alphaearth_routes(app)
register_land_verification_routes(app)

MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
RATE_WINDOW_SECONDS = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
EXPENSIVE_PATHS = {
    "/calculate", "/comprehensive-score", "/diagnose", "/spectral", "/spectral-indices", "/sar-moisture",
    "/historical-timeline", "/before-after", "/vegetation-heatmap", "/ndvi-heatmap", "/crop-intelligence",
    "/farm-advisor", "/risk-analysis", "/alphaearth", "/mandi-price", "/major-crops",
}
RATE_LIMITED_METHODS = {"POST", "GET"}
TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))
MAX_TRACKED_CLIENTS = int(os.getenv("MAX_TRACKED_CLIENTS", "10000"))
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(environ):
    if TRUSTED_PROXY_HOPS > 0:
        forwarded = environ.get("HTTP_X_FORWARDED_FOR", "")
        chain = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(chain) >= TRUSTED_PROXY_HOPS:
            return chain[-TRUSTED_PROXY_HOPS]
    return environ.get("REMOTE_ADDR", "unknown")


def _prune(now):
    cutoff = now - RATE_WINDOW_SECONDS
    for key in [k for k, b in _hits.items() if not b or b[-1] < cutoff]:
        del _hits[key]


def _rate_limited(environ):
    path = environ.get("PATH_INFO", "")
    if path not in EXPENSIVE_PATHS or environ.get("REQUEST_METHOD") not in RATE_LIMITED_METHODS:
        return False
    now = time.time()
    if len(_hits) > MAX_TRACKED_CLIENTS:
        _prune(now)
    key = f"{_client_ip(environ)}:{path}"
    bucket = _hits[key]
    cutoff = now - RATE_WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return True
    bucket.append(now)
    return False


def _response(start_response, status, body, headers=None):
    data = body.encode("utf-8")
    base = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(data)))]
    if headers:
        base.extend(headers)
    start_response(status, base)
    return [data]


def _land_verification_required(environ, start_response):
    """Production-side gate: /calculate requires a fresh token issued by
    /land-verification and the token must be bound to the same coordinates."""
    if environ.get("PATH_INFO") != "/calculate" or environ.get("REQUEST_METHOD") != "POST":
        return None
    length = environ.get("CONTENT_LENGTH")
    try:
        n = int(length or "0")
    except ValueError:
        return _response(start_response, "400 Bad Request", '{"error":"Invalid Content-Length"}')
    if n > MAX_REQUEST_BYTES:
        return _response(start_response, "413 Payload Too Large", '{"error":"Request too large"}')

    raw = environ["wsgi.input"].read(n) if n else b""
    environ["wsgi.input"] = io.BytesIO(raw)
    try:
        body = json.loads(raw.decode("utf-8"))
    except Exception:
        return _response(start_response, "400 Bad Request", '{"error":"Request body must be valid JSON"}')

    token = body.get("land_verification_token") if isinstance(body, dict) else None
    try:
        lat = float(body.get("lat"))
        lng = float(body.get("lng"))
    except (TypeError, ValueError, AttributeError):
        return _response(start_response, "400 Bad Request", '{"error":"Latitude and longitude are required"}')

    if not token or not verify_token(str(token), expected_lat=lat, expected_lng=lng):
        return _response(start_response, "403 Forbidden", '{"error":"Agricultural land verification is required for these coordinates before FarmScore can be calculated","code":"LAND_VERIFICATION_REQUIRED"}')
    return None


def middleware(environ, start_response):
    length = environ.get("CONTENT_LENGTH")
    try:
        if length and int(length) > MAX_REQUEST_BYTES:
            return _response(start_response, "413 Payload Too Large", '{"error":"Request too large"}')
    except ValueError:
        return _response(start_response, "400 Bad Request", '{"error":"Invalid Content-Length"}')

    land_gate_response = _land_verification_required(environ, start_response)
    if land_gate_response is not None:
        return land_gate_response
    if _rate_limited(environ):
        return _response(start_response, "429 Too Many Requests", '{"error":"Rate limit exceeded. Please wait before retrying."}', [("Retry-After", str(RATE_WINDOW_SECONDS))])

    def secured_start_response(status, headers, exc_info=None):
        filtered = [(k, v) for k, v in headers if k.lower() != "server"]
        filtered.extend([
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
        ])
        return start_response(status, filtered, exc_info)

    return app(environ, secured_start_response)


application = middleware
