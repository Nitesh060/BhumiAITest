"""AlphaEarth Foundations / Google Satellite Embedding service."""

from __future__ import annotations

import math
from typing import Any

import ee
from flask import jsonify, request

DATASET = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
BANDS = [f"A{i:02d}" for i in range(64)]
DEFAULT_YEAR = 2024
MIN_YEAR = 2017
MAX_YEAR = 2024


def _year_image(year: int, point: ee.Geometry) -> ee.Image:
    collection = (
        ee.ImageCollection(DATASET)
        .filterDate(f"{year}-01-01", f"{year + 1}-01-01")
        .filterBounds(point)
    )
    return collection.mosaic().select(BANDS)


def _safe_float(value: Any):
    if value is None:
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def fetch_alphaearth(lat: float, lng: float, year: int, compare_year: int | None = None) -> dict:
    point = ee.Geometry.Point([lng, lat])
    image = _year_image(year, point)

    sample = image.sample(region=point, scale=10, numPixels=1, geometries=False).first()
    values = sample.toDictionary(BANDS).getInfo() if sample else None
    if not values:
        raise ValueError(f"No AlphaEarth embedding found for {year} at this location")

    embedding = {band: _safe_float(values.get(band)) for band in BANDS}
    missing = [band for band, value in embedding.items() if value is None]
    if missing:
        raise ValueError(f"AlphaEarth pixel is masked or unavailable for {year}")

    # Google documents A01/A16/A09 as an example RGB visualization.
    vis = {"min": -0.3, "max": 0.3, "bands": ["A01", "A16", "A09"]}
    map_id = image.getMapId(vis)
    tile_url = map_id["tile_fetcher"].url_format

    result = {
        "dataset": DATASET,
        "model": "AlphaEarth Foundations",
        "year": year,
        "resolution_m": 10,
        "latitude": lat,
        "longitude": lng,
        "dimensions": 64,
        "embedding": embedding,
        "visualization": {
            "bands": ["A01", "A16", "A09"],
            "min": -0.3,
            "max": 0.3,
            "tile_url": tile_url,
        },
    }

    if compare_year is not None and compare_year != year:
        image2 = _year_image(compare_year, point)
        sample2 = image2.sample(region=point, scale=10, numPixels=1, geometries=False).first()
        values2 = sample2.toDictionary(BANDS).getInfo() if sample2 else None
        if values2:
            emb2 = [_safe_float(values2.get(band)) for band in BANDS]
            if all(value is not None for value in emb2):
                dot = sum(embedding[band] * emb2[i] for i, band in enumerate(BANDS))
                result["comparison"] = {
                    "year": compare_year,
                    "cosine_similarity": max(-1.0, min(1.0, dot)),
                    "change_score": max(0.0, min(1.0, 1.0 - dot)),
                }

    return result


def register_alphaearth_routes(app):
    @app.route("/alphaearth", methods=["GET"])
    def alphaearth_endpoint():
        try:
            lat = float(request.args.get("lat", ""))
            lng = float(request.args.get("lng", ""))
        except ValueError:
            return jsonify({"error": "Valid lat and lng are required"}), 400

        try:
            year = int(request.args.get("year", DEFAULT_YEAR))
            compare_year_raw = request.args.get("compare_year")
            compare_year = int(compare_year_raw) if compare_year_raw else None
        except ValueError:
            return jsonify({"error": "year and compare_year must be integers"}), 400

        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return jsonify({"error": "Latitude/longitude out of range"}), 400
        if not (MIN_YEAR <= year <= MAX_YEAR):
            return jsonify({"error": f"year must be between {MIN_YEAR} and {MAX_YEAR}"}), 400
        if compare_year is not None and not (MIN_YEAR <= compare_year <= MAX_YEAR):
            return jsonify({"error": f"compare_year must be between {MIN_YEAR} and {MAX_YEAR}"}), 400

        try:
            return jsonify(fetch_alphaearth(lat, lng, year, compare_year)), 200
        except Exception as exc:
            app.logger.exception("AlphaEarth request failed")
            return jsonify({"error": str(exc)}), 500

    return app
