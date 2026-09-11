"""
parcel_service.py
=================
Look up a pre-detected farm parcel boundary for a coordinate.

Where the parcels come from
---------------------------
An OFFLINE pipeline (see tools/load_parcels.py and the notes in
docs/PARCEL_PIPELINE.md) downloads high-resolution basemap imagery for an
AOI, runs SAM segmentation over it to find field boundaries, and writes the
resulting polygons here. None of that runs in this web service — it needs
rasterio/geopandas/scikit-image/GDAL and hours of GPU or CPU time per
district, which this Render dyno has neither the memory nor the request
budget for.

What this module does at request time is only the cheap half: one indexed
point-in-polygon query against Postgres.

Why it matters
--------------
Today `/calculate` only has a farm boundary when the user hand-draws one on
the map. Without it `compute_polygon_area_ha()` returns None, so farm area
is blank in the report and `verify_acreage()` in the insurance flow has
nothing to compare a declared area against. Resolving the parcel
automatically fills both.

Fails soft, always. If PostGIS is not installed, the table does not exist,
or nothing matches the coordinate, the caller gets `None` and the existing
hand-drawn-polygon path is unaffected.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy import text

from db import get_session, is_db_configured

logger = logging.getLogger(__name__)

# Parcels larger than this are almost certainly a segmentation failure —
# SAM occasionally returns a whole-scene mask that swallows every real field
# inside it. The offline pipeline filters these, but a bad load should not
# be able to hand a farmer a 500 ha "farm".
MAX_PLAUSIBLE_PARCEL_HA = 200.0

# Set once per process after the first successful query, so a missing
# PostGIS extension is reported once rather than on every request.
_availability: Optional[bool] = None


def _mark_unavailable(reason: str) -> None:
    global _availability
    if _availability is not False:
        logger.warning("Parcel lookup unavailable: %s", reason)
    _availability = False


def find_parcel(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """The detected parcel containing this point, or None.

    Returns the boundary as a GeoJSON geometry dict in the same shape
    `/calculate` already accepts for a hand-drawn polygon, so the caller can
    pass it straight through to the existing Earth Engine code.
    """
    global _availability

    if not is_db_configured() or _availability is False:
        return None

    session = None
    try:
        session = get_session()
        row = session.execute(
            text(
                """
                SELECT
                    id,
                    parcel_uid,
                    ST_AsGeoJSON(geom)            AS geojson,
                    ST_Area(geom::geography) / 10000.0 AS area_ha,
                    village_name,
                    village_code,
                    district,
                    source,
                    confidence
                FROM farm_parcels
                -- ST_Covers, not ST_Contains: ST_Contains is false for a
                -- point lying exactly ON the boundary, so a tap on a field
                -- edge — common, since that is where the visible bund is —
                -- found no parcel at all.
                WHERE ST_Covers(geom, ST_SetSRID(ST_Point(:lng, :lat), 4326))
                ORDER BY ST_Area(geom) ASC
                LIMIT 1
                """
            ),
            {"lat": float(lat), "lng": float(lng)},
        ).mappings().first()
        _availability = True
    except Exception as exc:
        message = str(exc).lower()
        if "farm_parcels" in message and "exist" in message:
            _mark_unavailable("farm_parcels table not created — run tools/load_parcels.py")
        elif "st_contains" in message or "postgis" in message:
            _mark_unavailable("PostGIS extension not enabled — run CREATE EXTENSION postgis")
        else:
            logger.exception("Parcel lookup failed for %.5f,%.5f", lat, lng)
        return None
    finally:
        if session is not None:
            session.close()

    if row is None:
        return None

    area_ha = float(row["area_ha"]) if row["area_ha"] is not None else None
    if area_ha is not None and area_ha > MAX_PLAUSIBLE_PARCEL_HA:
        logger.warning(
            "Ignoring implausible parcel id=%s (%.1f ha) at %.5f,%.5f",
            row["id"], area_ha, lat, lng,
        )
        return None

    try:
        geometry = json.loads(row["geojson"])
    except (TypeError, ValueError):
        logger.exception("Parcel id=%s has unparseable geometry", row["id"])
        return None

    return {
        "parcel_id": row["id"],
        "parcel_uid": row["parcel_uid"],
        "geometry": geometry,
        "area_ha": round(area_ha, 4) if area_ha is not None else None,
        "village_name": row["village_name"],
        "village_code": row["village_code"],
        "district": row["district"],
        "source": row["source"],
        "confidence": row["confidence"],
    }


SCHEMA_SQL = """
-- Run once against the Neon database. Requires the PostGIS extension;
-- Neon supports it but it is not enabled by default on a new project.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS farm_parcels (
    id            BIGSERIAL PRIMARY KEY,
    -- Deterministic identity: sha1 of the village code + the parcel's own
    -- geometry. Re-running the loader over the same imagery produces the
    -- same uid, so ON CONFLICT updates the row in place. Without this the
    -- table had no unique key at all and a second load simply duplicated
    -- every parcel — and the --replace flag was no protection, because it
    -- deletes with `WHERE village_code = :code`, which matches nothing when
    -- no --village-code was passed. It would report "deleted 0" and then
    -- insert a full duplicate set.
    parcel_uid    TEXT UNIQUE NOT NULL,
    -- 4326 so a raw lat/lng from the map can be tested directly. Area is
    -- computed with a ::geography cast at query time, which is geodesic —
    -- ST_Area on a 4326 geometry returns square DEGREES, which is
    -- meaningless as an area and varies with latitude.
    geom          geometry(MultiPolygon, 4326) NOT NULL,
    village_name  TEXT,
    village_code  TEXT,
    district      TEXT,
    source        TEXT NOT NULL,
    confidence    DOUBLE PRECISION,
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Without this the containment query is a full table scan; a district can
-- hold a few hundred thousand parcels.
CREATE INDEX IF NOT EXISTS farm_parcels_geom_idx ON farm_parcels USING GIST (geom);
CREATE INDEX IF NOT EXISTS farm_parcels_village_idx ON farm_parcels (village_code);
"""
