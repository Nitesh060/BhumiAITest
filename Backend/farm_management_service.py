"""
farm_management_service.py
============================
Phase 1: Farmer/Farm CRUD + KML/GeoJSON boundary import.

All functions take a SQLAlchemy session as their first argument
(caller opens/closes it — see app.py routes) so this module has no
hidden global session state and is easy to test.
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models import Farmer, Farm
from yield_prediction import compute_polygon_area_ha

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Farmer CRUD
# ---------------------------------------------------------------------------

def create_farmer(db: Session, name: str, phone: str = None, village: str = None,
                   district: str = None, state: str = None) -> Farmer:
    farmer = Farmer(name=name, phone=phone, village=village, district=district, state=state)
    db.add(farmer)
    db.commit()
    db.refresh(farmer)
    return farmer


def list_farmers(db: Session, search: Optional[str] = None) -> List[Farmer]:
    q = db.query(Farmer)
    if search:
        like = f"%{search}%"
        q = q.filter((Farmer.name.ilike(like)) | (Farmer.phone.ilike(like)) | (Farmer.village.ilike(like)))
    return q.order_by(Farmer.created_at.desc()).all()


def get_farmer(db: Session, farmer_id: str) -> Optional[Farmer]:
    return db.query(Farmer).filter(Farmer.id == farmer_id).first()


# Only these keys are settable via update_farmer/update_farm's **fields —
# previously any key from the client's JSON body was applied via
# `setattr(obj, k, v)` as long as `hasattr(obj, k)` was true, which is
# true for FAR more than the intended editable columns. On Farmer, that
# included the `farms` relationship itself (cascade="all, delete-orphan"
# in models.py) — a plain `PUT /farmers/<id>` with `{"farms": []}`, from
# ANY authenticated role (not just admin), silently deleted every Farm
# row for that farmer, bypassing the admin-only delete rule enforced
# elsewhere. On Farm, `farmer_id` could be overwritten the same way,
# reassigning a farm to a different farmer (IDOR-style). Only real,
# user-editable columns are allowed now.
_FARMER_UPDATABLE_FIELDS = {"name", "phone", "village", "district", "state"}
_FARM_UPDATABLE_FIELDS = {"label", "lat", "lng", "survey_method", "land_use_type", "survey_number"}


def update_farmer(db: Session, farmer_id: str, **fields) -> Optional[Farmer]:
    farmer = get_farmer(db, farmer_id)
    if not farmer:
        return None
    for k, v in fields.items():
        if k in _FARMER_UPDATABLE_FIELDS and v is not None:
            setattr(farmer, k, v)
    db.commit()
    db.refresh(farmer)
    return farmer


def delete_farmer(db: Session, farmer_id: str) -> bool:
    farmer = get_farmer(db, farmer_id)
    if not farmer:
        return False
    db.delete(farmer)  # cascades to farms, see models.py relationship
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Farm CRUD
# ---------------------------------------------------------------------------

def create_farm(db: Session, farmer_id: str, lat: float, lng: float, label: str = None,
                 polygon: Optional[dict] = None, survey_method: str = "point_only",
                 land_use_type: str = None, survey_number: str = None) -> Optional[Farm]:
    farmer = get_farmer(db, farmer_id)
    if not farmer:
        return None

    area_ha = None
    polygon_json = None
    if polygon:
        polygon_json = json.dumps(polygon)
        try:
            area_ha = compute_polygon_area_ha(polygon)
        except Exception:
            logger.exception("Area computation failed for new farm (non-fatal)")

    farm = Farm(
        farmer_id=farmer_id, lat=lat, lng=lng, label=label,
        polygon_geojson=polygon_json, area_ha=area_ha,
        survey_method=survey_method, land_use_type=land_use_type,
        survey_number=survey_number,
    )
    db.add(farm)
    db.commit()
    db.refresh(farm)
    return farm


def list_farms_for_farmer(db: Session, farmer_id: str) -> List[Farm]:
    return db.query(Farm).filter(Farm.farmer_id == farmer_id).order_by(Farm.created_at.desc()).all()


def get_farm(db: Session, farm_id: str) -> Optional[Farm]:
    return db.query(Farm).filter(Farm.id == farm_id).first()


def update_farm(db: Session, farm_id: str, **fields) -> Optional[Farm]:
    farm = get_farm(db, farm_id)
    if not farm:
        return None
    if "polygon" in fields:
        polygon = fields.pop("polygon")
        if polygon:
            farm.polygon_geojson = json.dumps(polygon)
            try:
                farm.area_ha = compute_polygon_area_ha(polygon)
            except Exception:
                logger.exception("Area recomputation failed on farm update (non-fatal)")
    for k, v in fields.items():
        if k in _FARM_UPDATABLE_FIELDS and v is not None:
            setattr(farm, k, v)
    db.commit()
    db.refresh(farm)
    return farm


def delete_farm(db: Session, farm_id: str) -> bool:
    farm = get_farm(db, farm_id)
    if not farm:
        return False
    db.delete(farm)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# KML / GeoJSON import — extracts a polygon (coordinates array in
# [[[lng,lat],[lng,lat],...]] format, matching what the rest of this app
# already expects from the frontend's Leaflet.draw output).
# ---------------------------------------------------------------------------

def parse_geojson_polygon(file_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Accepts a .geojson/.json file (a bare Polygon geometry, a Feature,
    or a FeatureCollection) and returns a {"type": "Polygon",
    "coordinates": [...]} dict, or None if no polygon found.
    """
    try:
        data = json.loads(file_bytes.decode("utf-8"))
    except Exception:
        logger.exception("Invalid GeoJSON file")
        return None

    geom = None
    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
        if features:
            geom = features[0].get("geometry")
    elif data.get("type") == "Feature":
        geom = data.get("geometry")
    elif data.get("type") == "Polygon":
        geom = data

    if not geom or geom.get("type") != "Polygon":
        logger.warning("GeoJSON import: no Polygon geometry found")
        return None

    return {"type": "Polygon", "coordinates": geom["coordinates"]}


def parse_kml_polygon(file_bytes: bytes) -> Optional[Dict[str, Any]]:
    """Extracts the first Polygon's outer boundary from a KML file using
    the standard library's XML parser (no extra dependency). Returns a
    {"type": "Polygon", "coordinates": [...]} dict, or None if no
    polygon is found.
    """
    try:
        ns = {"kml": "http://www.opengis.net/kml/2.2"}
        root = ET.fromstring(file_bytes)

        coords_el = root.find(".//kml:Polygon//kml:outerBoundaryIs//kml:LinearRing//kml:coordinates", ns)
        if coords_el is None:
            # Some KML exports omit the namespace — retry without it
            coords_el = root.find(".//Polygon//outerBoundaryIs//LinearRing//coordinates")

        if coords_el is None or not coords_el.text:
            logger.warning("KML import: no <Polygon><coordinates> found")
            return None

        ring = []
        for triplet in coords_el.text.strip().split():
            parts = triplet.split(",")
            lng, lat = float(parts[0]), float(parts[1])
            ring.append([lng, lat])

        if ring[0] != ring[-1]:
            ring.append(ring[0])  # close the ring, GeoJSON polygons must be closed

        return {"type": "Polygon", "coordinates": [ring]}
    except Exception:
        logger.exception("KML parsing failed")
        return None


# ---------------------------------------------------------------------------
# Auto Boundary Detection — future-ready stub (roadmap Phase 1 item).
# Real implementation needs a trained field-boundary segmentation model
# (e.g. on Sentinel-2 + SAR) — not built yet. This returns a clear
# "not implemented" response instead of a fake result.
# ---------------------------------------------------------------------------

def auto_detect_boundary(lat: float, lng: float) -> Dict[str, Any]:
    return {
        "available": False,
        "reason": "Auto boundary detection needs a trained field-segmentation model — not built yet. "
                   "Use manual draw, GPS walking survey, or KML/GeoJSON import for now.",
    }
