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

register_alphaearth_routes(app)

# The seasonal (Kharif + Rabi) data-pipeline rebinding that used to live
# here — reassigning app_module.fetch_farm_data/fetch_spi/etc. on this
# imported module's globals, from the outside, after import — has moved
# into app.py itself, right above compute_farmscore(). It was invisible
# here to anyone reading app.py (or the "real" per-parameter modules it
# imports) on its own, which is why an earlier fix to
# weather_indices_service.py's SPI/solar-radiation logic had zero effect
# on the live site: those functions were never actually being called,
# only their seasonal replacements — defined and rebound in this file —
# were. See app.py for the current (explicit) version of this logic.


MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
RATE_WINDOW_SECONDS = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
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
        # Only strip the "Server" header (info-leak hardening) — CORS
        # headers are left exactly as Flask-CORS set them. This used to
        # also strip Access-Control-Allow-Origin/-Credentials and only
        # re-add them for a single exact-match ALLOWED_ORIGIN, which broke
        # the Frontend entirely whenever that env var wasn't set (every
        # cross-origin response silently lost its CORS header — the
        # "Failed to fetch" bug documented in wsgi_render.py). app.py's own
        # CORS(app, resources=...) now reads the same ALLOWED_ORIGIN env
        # var and is the single source of truth for which origins are
        # allowed — this middleware no longer needs to duplicate that
        # logic, just add the extra hardening headers on top of it.
        filtered = [(k, v) for k, v in headers if k.lower() != "server"]
        filtered.extend([
            ("X-Content-Type-Options", "nosniff"), ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
        ])
        return start_response(status, filtered, exc_info)

    return app(environ, secured_start_response)


application = middleware
