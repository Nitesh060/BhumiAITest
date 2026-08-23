"""Tests for ground_truth_service.py — the field-officer data-capture
flow from ROADMAP.md Phase 8 (labeled-data bootstrap for Phase 11's
trained ML models). Uses an in-memory SQLite DB, matching the
convention in test_audit_access_control.py.
"""
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Base
import farm_management_service as fms
import ground_truth_service as gts


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def farm(db_session):
    farmer = fms.create_farmer(db_session, name="Ram")
    return fms.create_farm(db_session, farmer.id, lat=10.0, lng=20.0, label="Plot 1")


class TestCreateObservation:
    def test_creates_a_basic_observation(self, db_session, farm):
        obs = gts.create_observation(db_session, farm_id=farm.id, crop="Wheat")
        assert obs.id is not None
        assert obs.crop == "Wheat"
        assert obs.farm_id == farm.id

    def test_strips_whitespace_from_crop(self, db_session, farm):
        obs = gts.create_observation(db_session, farm_id=farm.id, crop="  Rice  ")
        assert obs.crop == "Rice"

    def test_records_full_fields(self, db_session, farm):
        obs = gts.create_observation(
            db_session, farm_id=farm.id, crop="Cotton", season="kharif",
            sowing_date="2026-06-15", harvest_date="2026-11-01",
            observed_yield_kg_per_acre=450.5, notes="Good rainfall this season",
            recorded_by_user_id="officer-1",
        )
        assert obs.season == "kharif"
        assert obs.sowing_date.isoformat().startswith("2026-06-15")
        assert obs.harvest_date.isoformat().startswith("2026-11-01")
        assert obs.observed_yield_kg_per_acre == 450.5
        assert obs.notes == "Good rainfall this season"
        assert obs.recorded_by_user_id == "officer-1"

    def test_invalid_date_string_is_ignored_not_raised(self, db_session, farm):
        """A malformed date shouldn't block recording real ground truth
        — it's better to save the observation without that one field
        than to lose the whole submission."""
        obs = gts.create_observation(db_session, farm_id=farm.id, crop="Maize", sowing_date="not-a-date")
        assert obs.sowing_date is None

    def test_stores_photo_as_base64(self, db_session, farm):
        obs = gts.create_observation(
            db_session, farm_id=farm.id, crop="Soybean",
            photo_bytes=b"\xff\xd8\xff\xe0fake-jpeg-bytes", photo_mime_type="image/jpeg",
        )
        assert obs.photo_data_b64 is not None
        assert obs.photo_mime_type == "image/jpeg"

    def test_rejects_oversized_photo(self, db_session, farm):
        oversized = b"x" * (gts.MAX_PHOTO_BYTES + 1)
        with pytest.raises(ValueError):
            gts.create_observation(db_session, farm_id=farm.id, crop="Wheat", photo_bytes=oversized)

    def test_no_photo_means_no_photo_fields_set(self, db_session, farm):
        obs = gts.create_observation(db_session, farm_id=farm.id, crop="Wheat")
        assert obs.photo_data_b64 is None
        assert obs.photo_mime_type is None


class TestListAndGetObservations:
    def test_list_returns_only_this_farms_observations(self, db_session):
        farmer = fms.create_farmer(db_session, name="Sita")
        farm_a = fms.create_farm(db_session, farmer.id, lat=1.0, lng=2.0)
        farm_b = fms.create_farm(db_session, farmer.id, lat=3.0, lng=4.0)
        gts.create_observation(db_session, farm_id=farm_a.id, crop="Wheat")
        gts.create_observation(db_session, farm_id=farm_b.id, crop="Rice")

        results = gts.list_observations_for_farm(db_session, farm_a.id)

        assert len(results) == 1
        assert results[0].crop == "Wheat"

    def test_list_orders_newest_first(self, db_session, farm):
        first = gts.create_observation(db_session, farm_id=farm.id, crop="Wheat")
        second = gts.create_observation(db_session, farm_id=farm.id, crop="Rice")

        results = gts.list_observations_for_farm(db_session, farm.id)

        assert [r.id for r in results] == [second.id, first.id]

    def test_get_observation_returns_none_when_missing(self, db_session):
        assert gts.get_observation(db_session, "does-not-exist") is None

    def test_get_observation_returns_the_record(self, db_session, farm):
        obs = gts.create_observation(db_session, farm_id=farm.id, crop="Wheat")
        assert gts.get_observation(db_session, obs.id).id == obs.id


class TestToDict:
    def test_excludes_photo_by_default(self, db_session, farm):
        obs = gts.create_observation(
            db_session, farm_id=farm.id, crop="Wheat",
            photo_bytes=b"fake-bytes", photo_mime_type="image/png",
        )
        d = obs.to_dict()
        assert d["has_photo"] is True
        assert "photo_data_b64" not in d

    def test_includes_photo_when_requested(self, db_session, farm):
        obs = gts.create_observation(
            db_session, farm_id=farm.id, crop="Wheat",
            photo_bytes=b"fake-bytes", photo_mime_type="image/png",
        )
        d = obs.to_dict(include_photo=True)
        assert d["photo_data_b64"] is not None
        assert d["photo_mime_type"] == "image/png"
