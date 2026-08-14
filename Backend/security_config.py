"""Security helpers for the Bhumi API."""
from __future__ import annotations

import os


def allowed_origin() -> str:
    return os.getenv("ALLOWED_ORIGIN", "https://nitesh060.github.io")


def require_production_secrets() -> None:
    if os.getenv("FLASK_DEBUG", "0") == "1":
        return
    required = ("JWT_SECRET",)
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing required production secrets: " + ", ".join(missing))
