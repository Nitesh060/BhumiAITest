"""Render WSGI entrypoint.

Loads the existing production setup from wsgi.py (including seasonal
parameter bindings and AlphaEarth routes), but exposes the Flask app
itself so Flask-CORS can emit the Access-Control-Allow-Origin header.
The previous middleware stripped Flask-CORS headers unless ALLOWED_ORIGIN
was configured, which caused browser-side `Failed to fetch` errors.
"""

import wsgi as _production_wsgi

# wsgi.py performs the required route registration and seasonal rebinding
# during import. Use the Flask application directly so its CORS headers
# are preserved instead of being removed by the legacy wrapper.
application = _production_wsgi.app
