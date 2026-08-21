"""Render WSGI entrypoint.

Loads the existing production setup from wsgi.py (route registration,
seasonal parameter bindings, AlphaEarth routes) and its `middleware`
wrapper, which adds request-size limiting, per-IP rate limiting on the
expensive satellite/Gemini endpoints, and hardening response headers.

This used to bypass `middleware` and expose the bare Flask `app` instead,
because the middleware's CORS handling stripped Flask-CORS's own headers
unless ALLOWED_ORIGIN was set, breaking the Frontend with `Failed to
fetch`. That's now fixed at the source (wsgi.py's middleware no longer
touches CORS headers at all — app.py's own CORS(...) is the single
source of truth for allowed origins), so the full middleware — including
rate limiting, which was silently disabled for however long this bypass
was in place — is back in front of every request.
"""

import wsgi as _production_wsgi

application = _production_wsgi.application
