"""Production WSGI wrapper for Bhumi AI."""

from __future__ import annotations

import os
import time
from collections import defaultdict, deque

from app import app

MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))
RATE_WINDOW_SECONDS = int(os.getenv("RATE_WINDOW_SECONDS", "60"))
RATE_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "").strip()
EXPENSIVE_PATHS = {
    "/calculate", "/comprehensive-score", "/diagnose", "/spectral",
    "/spectral-indices", "/sar-moisture", "/historical-timeline",
    "/before-after", "/vegetation-heatmap", "/ndvi-heatmap",
    "/crop-intelligence", "/farm-advisor", "/risk-analysis",
}
_hits: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(environ):
    return environ.get("REMOTE_ADDR", "unknown")


def _rate_limited(environ):
    path = environ.get("PATH_INFO", "")
    if path not in EXPENSIVE_PATHS or environ.get("REQUEST_METHOD") != "POST":
        return False
    now = time.time()
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
            filtered.append(("Access-Control-Allow-Origin", ALLOWED_ORIGIN))
            filtered.append(("Vary", "Origin"))
        filtered.extend([
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "SAMEORIGIN"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=()"),
        ])
        return start_response(status, filtered, exc_info)

    return app(environ, secured_start_response)


application = middleware
