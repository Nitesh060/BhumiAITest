"""
ground_truth_service.py
=========================
Field-officer-recorded ground truth: a farm's real observed crop
identity and yield for one season, optionally with a photo. See
ROADMAP.md Phase 8 — this is the labeled-data bootstrap every trained
ML model (Phase 11's real M1 crop-ID and M3 yield models) needs, and
there was previously no capture pipeline for it at all.

All functions take a SQLAlchemy session as their first argument
(caller opens/closes it — see app.py routes), matching
farm_management_service.py's convention.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from models import GroundTruthObservation

logger = logging.getLogger(__name__)

# Photos are stored as base64 directly in Postgres (see
# models.GroundTruthObservation's docstring for why) — capped here to
# keep any one record, and the table overall, from growing unbounded.
MAX_PHOTO_BYTES = 2 * 1024 * 1024  # 2 MB


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def create_observation(
    db: Session,
    farm_id: str,
    crop: str,
    season: Optional[str] = None,
    sowing_date: Optional[str] = None,
    harvest_date: Optional[str] = None,
    observed_yield_kg_per_acre: Optional[float] = None,
    notes: Optional[str] = None,
    photo_bytes: Optional[bytes] = None,
    photo_mime_type: Optional[str] = None,
    recorded_by_user_id: Optional[str] = None,
) -> GroundTruthObservation:
    """Raises ValueError if photo_bytes exceeds MAX_PHOTO_BYTES — the
    caller (app.py) turns that into a 400, matching how the rest of
    this app validates uploaded images before they reach a service
    function.
    """
    photo_b64 = None
    if photo_bytes:
        if len(photo_bytes) > MAX_PHOTO_BYTES:
            raise ValueError(f"Photo too large (max {MAX_PHOTO_BYTES // (1024 * 1024)} MB)")
        photo_b64 = base64.b64encode(photo_bytes).decode("ascii")

    obs = GroundTruthObservation(
        farm_id=farm_id,
        crop=crop.strip(),
        season=season,
        sowing_date=_parse_date(sowing_date),
        harvest_date=_parse_date(harvest_date),
        observed_yield_kg_per_acre=observed_yield_kg_per_acre,
        notes=notes,
        photo_data_b64=photo_b64,
        photo_mime_type=photo_mime_type if photo_b64 else None,
        recorded_by_user_id=recorded_by_user_id,
    )
    db.add(obs)
    db.commit()
    db.refresh(obs)
    return obs


def list_observations_for_farm(db: Session, farm_id: str) -> List[GroundTruthObservation]:
    return (
        db.query(GroundTruthObservation)
        .filter(GroundTruthObservation.farm_id == farm_id)
        .order_by(GroundTruthObservation.created_at.desc())
        .all()
    )


def get_observation(db: Session, observation_id: str) -> Optional[GroundTruthObservation]:
    return db.query(GroundTruthObservation).filter(GroundTruthObservation.id == observation_id).first()
