"""
models.py
=========
Phase 1 data model: Farmer <-> Farm (one-to-many, multi-farm support).

Kept intentionally minimal for Phase 1 — just enough to register
farmers, register multiple farms per farmer, and store each farm's
boundary (GeoJSON polygon) + centroid. Loan/credit/insurance fields
are NOT here yet — those belong to Phase 6/7 and will be added as
their own tables/columns when those phases are built, so this table
doesn't need reshaping later for unrelated features.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Text, Integer
from sqlalchemy.orm import relationship

from db import Base


def _uuid():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=_uuid)
    username = Column(String, nullable=False, unique=True, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False, default="field_officer")  # "admin" | "field_officer"
    created_at = Column(DateTime, default=_now)

    def to_dict(self):
        return {
            "id": self.id, "username": self.username, "name": self.name,
            "role": self.role, "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Farmer(Base):
    __tablename__ = "farmers"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True, index=True)
    village = Column(String, nullable=True)
    district = Column(String, nullable=True)
    state = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    farms = relationship("Farm", back_populates="farmer", cascade="all, delete-orphan")

    def to_dict(self, include_farms: bool = False):
        d = {
            "id": self.id,
            "name": self.name,
            "phone": self.phone,
            "village": self.village,
            "district": self.district,
            "state": self.state,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "farm_count": len(self.farms) if self.farms is not None else 0,
        }
        if include_farms:
            d["farms"] = [f.to_dict() for f in self.farms]
        return d


class Farm(Base):
    __tablename__ = "farms"

    id = Column(String, primary_key=True, default=_uuid)
    farmer_id = Column(String, ForeignKey("farmers.id"), nullable=False, index=True)
    label = Column(String, nullable=True)  # e.g. "Farm 1", "North Plot"
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    polygon_geojson = Column(Text, nullable=True)  # raw GeoJSON polygon string, or NULL if point-only
    area_ha = Column(Float, nullable=True)
    survey_method = Column(String, nullable=True)  # "drawn" | "gps_walk" | "kml_import" | "geojson_import" | "point_only"
    land_use_type = Column(String, nullable=True)
    survey_number = Column(String, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    farmer = relationship("Farmer", back_populates="farms")

    def to_dict(self):
        import json
        polygon = None
        if self.polygon_geojson:
            try:
                polygon = json.loads(self.polygon_geojson)
            except Exception:
                polygon = None
        return {
            "id": self.id,
            "farmer_id": self.farmer_id,
            "label": self.label,
            "lat": self.lat,
            "lng": self.lng,
            "polygon": polygon,
            "area_ha": self.area_ha,
            "survey_method": self.survey_method,
            "land_use_type": self.land_use_type,
            "survey_number": self.survey_number,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
