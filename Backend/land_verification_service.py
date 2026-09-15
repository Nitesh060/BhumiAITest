"""Odisha cadastral verification gate for FarmScore.

The frontend obtains parcel metadata from the Odisha 4K GEO cadastral
source and sends the selected administrative/plot context here. This
service validates the workflow, classifies only explicit agricultural
land-use labels as eligible, and issues a short-lived signed token.

This is an eligibility gate, not a legal title/ROR certification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Dict

from flask import jsonify, request


ODISHA_BBOX = {
    "min_lat": 17.78,
    "max_lat": 22.65,
    "min_lng": 81.35,
    "max_lng": 87.55,
}

AGRICULTURAL_RE = __import__("re").compile(
    r"agri|agricult|cultiv|crop|paddy|kharif|rabi|garden|orchard|fallow|farm|plantation",
    __import__("re").IGNORECASE,
)


def _secret() -> bytes:
    value = os.getenv("LAND_VERIFY_SECRET") or os.getenv("SECRET_KEY") or "bhumi-land-verification-v1"
    return value.encode("utf-8")


def _is_agricultural(label: Any) -> bool:
    if label is None:
        return False
    return bool(AGRICULTURAL_RE.search(str(label)))


def _normalise_payload(body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": str(body.get("state") or "").strip(),
        "district": str(body.get("district") or "").strip(),
        "block": str(body.get("block") or body.get("tehsil") or "").strip(),
        "gp": str(body.get("gp") or "").strip(),
        "village": str(body.get("village") or "").strip(),
        "plot_no": str(body.get("plot_no") or "").strip(),
        "land_use": str(body.get("land_use") or "").strip(),
        "source": str(body.get("source") or "").strip(),
        "lat": float(body["lat"]),
        "lng": float(body["lng"]),
    }


def _sign(payload: Dict[str, Any], issued_at: int) -> str:
    message = json.dumps({"v": payload, "iat": issued_at}, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(_secret(), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(
        json.dumps({"v": payload, "iat": issued_at, "sig": base64.urlsafe_b64encode(digest).decode("ascii")}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def verify_token(token: str, max_age: int = 900) -> bool:
    try:
        outer = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
        issued_at = int(outer["iat"])
        if time.time() - issued_at > max_age or issued_at > time.time() + 60:
            return False
        payload = outer["v"]
        expected = _sign(payload, issued_at)
        return hmac.compare_digest(expected, token)
    except Exception:
        return False


def register_land_verification_routes(app) -> None:
    @app.post("/land-verification")
    def land_verification():
        body = request.get_json(silent=True) or {}
        try:
            data = _normalise_payload(body)
        except (KeyError, TypeError, ValueError):
            return jsonify({"verified": False, "error": "Valid latitude and longitude are required"}), 400

        required = ["state", "district", "block", "village", "plot_no", "land_use", "source"]
        missing = [key for key in required if not data[key]]
        if missing:
            return jsonify({"verified": False, "error": "Missing verification fields", "missing": missing}), 400

        if data["state"].casefold() != "odisha":
            return jsonify({"verified": False, "error": "This verification gate currently supports Odisha only"}), 400

        if data["source"].casefold() != "odisha 4k geo":
            return jsonify({"verified": False, "error": "Parcel must be verified from Odisha 4K GEO cadastral data"}), 400

        if not (ODISHA_BBOX["min_lat"] <= data["lat"] <= ODISHA_BBOX["max_lat"] and ODISHA_BBOX["min_lng"] <= data["lng"] <= ODISHA_BBOX["max_lng"]):
            return jsonify({"verified": False, "error": "Selected coordinates are outside the Odisha verification area"}), 400

        agricultural = _is_agricultural(data["land_use"])
        issued_at = int(time.time())

        if not agricultural:
            return jsonify({
                "verified": True,
                "agricultural": False,
                "farm_score_allowed": False,
                "land_use": data["land_use"],
                "source": data["source"],
                "message": "Selected parcel is not classified as agricultural land in the selected cadastral metadata. FarmScore is blocked.",
            }), 200

        token = _sign(data, issued_at)
        return jsonify({
            "verified": True,
            "agricultural": True,
            "farm_score_allowed": True,
            "land_use": data["land_use"],
            "source": data["source"],
            "verification_token": token,
            "expires_in_seconds": 900,
            "message": "Agricultural land-use classification verified from Odisha 4K GEO metadata. This is not a legal title/ROR certification.",
        }), 200
