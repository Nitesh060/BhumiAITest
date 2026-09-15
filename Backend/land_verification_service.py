"""Odisha cadastral verification and administrative lookup services."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Dict

import requests
from flask import jsonify, request

ODISHA_BBOX = {"min_lat": 17.78, "max_lat": 22.65, "min_lng": 81.35, "max_lng": 87.55}
ODISHA_GIS = "https://webgis1.nic.in/publishing/rest/services/odisha/odisha/MapServer"
ALLOWED_ADMIN_LAYERS = {"0", "1", "2", "3", "4"}
AGRICULTURAL_RE = re.compile(r"agri|agricult|cultiv|crop|paddy|kharif|rabi|garden|orchard|fallow|farm|plantation", re.IGNORECASE)


def _secret() -> bytes:
    value = os.getenv("LAND_VERIFY_SECRET") or os.getenv("SECRET_KEY") or "bhumi-land-verification-v1"
    return value.encode("utf-8")


def _is_agricultural(label: Any) -> bool:
    return bool(label is not None and AGRICULTURAL_RE.search(str(label)))


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
    outer = {"v": payload, "iat": issued_at, "sig": base64.urlsafe_b64encode(digest).decode("ascii")}
    return base64.urlsafe_b64encode(json.dumps(outer, separators=(",", ":")).encode("utf-8")).decode("ascii")


def verify_token(token: str, expected_lat: float | None = None, expected_lng: float | None = None, max_age: int = 900) -> bool:
    try:
        outer = json.loads(base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8"))
        issued_at = int(outer["iat"])
        if time.time() - issued_at > max_age or issued_at > time.time() + 60:
            return False
        payload = outer["v"]
        expected = _sign(payload, issued_at)
        if not hmac.compare_digest(expected, token):
            return False
        if expected_lat is not None and abs(float(payload["lat"]) - float(expected_lat)) > 1e-5:
            return False
        if expected_lng is not None and abs(float(payload["lng"]) - float(expected_lng)) > 1e-5:
            return False
        return True
    except Exception:
        return False


def register_land_verification_routes(app) -> None:
    @app.get("/odisha-admin/<layer_id>")
    def odisha_admin_proxy(layer_id: str):
        """Proxy the official Odisha NIC ArcGIS administrative layers.

        The browser cannot reliably call this NIC endpoint because of its
        cross-origin policy. Keeping the proxy server-side also lets us keep
        the official service URL in one place and whitelist only layers 0-4.
        """
        if layer_id not in ALLOWED_ADMIN_LAYERS:
            return jsonify({"error": "Unsupported Odisha administrative layer"}), 404
        params = {
            "f": request.args.get("f", "json"),
            "where": request.args.get("where", "1=1"),
            "outFields": request.args.get("outFields", "*"),
            "returnGeometry": request.args.get("returnGeometry", "false"),
            "outSR": request.args.get("outSR", "4326"),
            "resultRecordCount": request.args.get("resultRecordCount", "2000"),
        }
        # Layer metadata requests do not use the query parameters.
        if request.args.get("metadata") == "1":
            params = {"f": "json"}
            url = f"{ODISHA_GIS}/{layer_id}"
        else:
            url = f"{ODISHA_GIS}/{layer_id}/query"
        try:
            upstream = requests.get(url, params=params, timeout=20)
            upstream.raise_for_status()
            payload = upstream.json()
            if isinstance(payload, dict) and payload.get("error"):
                return jsonify(payload), 502
            return jsonify(payload)
        except requests.RequestException as exc:
            return jsonify({"error": "Odisha GIS service unavailable", "detail": str(exc)}), 502
        except ValueError:
            return jsonify({"error": "Odisha GIS returned invalid JSON"}), 502

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

        if not _is_agricultural(data["land_use"]):
            return jsonify({
                "verified": True, "agricultural": False, "farm_score_allowed": False,
                "land_use": data["land_use"], "source": data["source"],
                "message": "Selected parcel is not classified as agricultural land in the selected cadastral metadata. FarmScore is blocked.",
            }), 200

        token = _sign(data, int(time.time()))
        return jsonify({
            "verified": True, "agricultural": True, "farm_score_allowed": True,
            "land_use": data["land_use"], "source": data["source"],
            "verification_token": token, "expires_in_seconds": 900,
            "message": "Agricultural land-use classification verified from Odisha 4K GEO metadata. This is not a legal title/ROR certification.",
        }), 200
