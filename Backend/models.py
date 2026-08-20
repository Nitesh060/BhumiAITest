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


class AuditLog(Base):
    """Records every credit/insurance decision this app makes — BCIS
    scores, loan ceilings, auto-freezes, claim assessments. Matches the
    Bhumi doc's 'Evidence pack for every scored loan retained for 7
    years' / 'Audit trail for every auto-freeze event' requirement.
    Never edited or deleted after creation — append-only by design.
    """
    __tablename__ = "audit_logs"

    id = Column(String, primary_key=True, default=_uuid)
    event_type = Column(String, nullable=False, index=True)  # "bcis_score" | "loan_ceiling" | "auto_freeze" | "insurance_claim" | "consent_change" | "deletion_request" | "loan_stage_change"
    user_id = Column(String, nullable=True, index=True)       # who triggered it (from the JWT), null if unauthenticated
    farmer_id = Column(String, nullable=True, index=True)
    farm_id = Column(String, nullable=True, index=True)
    loan_id = Column(String, nullable=True, index=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    summary = Column(Text, nullable=True)       # short human-readable line, e.g. "BCIS 72/100 RED, loan ceiling Rs0"
    detail_json = Column(Text, nullable=True)   # full response payload, as JSON text, for the evidence pack
    created_at = Column(DateTime, default=_now, index=True)

    def to_dict(self, include_detail: bool = False):
        import json
        d = {
            "id": self.id, "event_type": self.event_type, "user_id": self.user_id,
            "farmer_id": self.farmer_id, "farm_id": self.farm_id, "loan_id": self.loan_id,
            "lat": self.lat, "lng": self.lng, "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_detail and self.detail_json:
            try:
                d["detail"] = json.loads(self.detail_json)
            except Exception:
                d["detail"] = None
        return d


class Consent(Base):
    """One row per farmer per consent type. Matches the Bhumi doc's
    'Separate consents for: Copilot advisory, loan data use, insurance
    data use, photo storage for AI training' + 'STOP command honoured
    immediately' + 'Deletion on request within 72 hours' requirements.
    """
    __tablename__ = "consents"

    id = Column(String, primary_key=True, default=_uuid)
    farmer_id = Column(String, ForeignKey("farmers.id"), nullable=False, index=True)
    consent_type = Column(String, nullable=False)  # "advisory" | "loan_data" | "insurance_data" | "photo_storage"
    granted = Column(String, nullable=False, default="pending")  # "granted" | "revoked" | "pending"
    granted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    deletion_requested_at = Column(DateTime, nullable=True)
    notes = Column(String, nullable=True)  # e.g. "requested via WhatsApp STOP", "recorded by officer at onboarding"
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    def to_dict(self):
        return {
            "id": self.id, "farmer_id": self.farmer_id, "consent_type": self.consent_type,
            "granted": self.granted,
            "granted_at": self.granted_at.isoformat() if self.granted_at else None,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "deletion_requested_at": self.deletion_requested_at.isoformat() if self.deletion_requested_at else None,
            "notes": self.notes,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Loan(Base):
    """Tracks a loan through the 5-stage lifecycle from the Bhumi doc:
    Application -> Disbursement -> In-Season -> Pre-Harvest -> Renewal.
    """
    __tablename__ = "loans"

    id = Column(String, primary_key=True, default=_uuid)
    farmer_id = Column(String, ForeignKey("farmers.id"), nullable=False, index=True)
    farm_id = Column(String, ForeignKey("farms.id"), nullable=True, index=True)
    stage = Column(String, nullable=False, default="Application")
    requested_amount_rs = Column(Float, nullable=True)
    approved_ceiling_rs = Column(Float, nullable=True)
    bcis_tier_at_approval = Column(String, nullable=True)
    crop = Column(String, nullable=True)
    season = Column(String, nullable=True)  # "kharif" | "rabi"
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    def to_dict(self):
        return {
            "id": self.id, "farmer_id": self.farmer_id, "farm_id": self.farm_id,
            "stage": self.stage, "requested_amount_rs": self.requested_amount_rs,
            "approved_ceiling_rs": self.approved_ceiling_rs, "bcis_tier_at_approval": self.bcis_tier_at_approval,
            "crop": self.crop, "season": self.season,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


LOAN_STAGES = ["Application", "Disbursement", "In-Season", "Pre-Harvest", "Renewal"]


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
