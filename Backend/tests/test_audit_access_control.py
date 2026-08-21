"""Regression tests for the access-control audit fixes:

1. farm_management_service.update_farmer/update_farm used to apply ANY
   client-supplied JSON key via setattr(obj, k, v) as long as
   hasattr(obj, k) — including `farms` (a cascade="all, delete-orphan"
   relationship) and `farmer_id` (letting a farm be reassigned to a
   different farmer). Both are now restricted to an explicit allowlist.
2. app.py's /credit-intelligence trusted a client-supplied `score`
   outright — now requires an HMAC signature /calculate itself attaches.
3. pdf_report._safe_image fetched whatever URL a client-supplied
   satellite_thumbnail named (blind SSRF) — now only accepts
   *.googleapis.com hosts.
"""
import os
import sys
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import Base
import farm_management_service as fms
import pdf_report


# ---------------------------------------------------------------------------
# Mass-assignment allowlist (in-memory sqlite, independent of DATABASE_URL)
# ---------------------------------------------------------------------------

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class TestMassAssignmentAllowlist:
    def test_update_farmer_applies_allowed_fields(self, db_session):
        farmer = fms.create_farmer(db_session, name="Ram", village="Old")
        updated = fms.update_farmer(db_session, farmer.id, name="Ramesh", village="New")
        assert updated.name == "Ramesh"
        assert updated.village == "New"

    def test_update_farmer_ignores_farms_relationship(self, db_session):
        """The actual production bug: {"farms": []} used to delete every
        Farm row for this farmer via a plain update call, from any role."""
        farmer = fms.create_farmer(db_session, name="Sita")
        fms.create_farm(db_session, farmer.id, lat=10.0, lng=20.0, label="Plot 1")
        assert len(fms.list_farms_for_farmer(db_session, farmer.id)) == 1

        fms.update_farmer(db_session, farmer.id, farms=[])

        assert len(fms.list_farms_for_farmer(db_session, farmer.id)) == 1

    def test_update_farmer_ignores_id_and_timestamps(self, db_session):
        farmer = fms.create_farmer(db_session, name="Gita")
        original_id = farmer.id
        fms.update_farmer(db_session, farmer.id, id="attacker-chosen-id", created_at="2000-01-01")
        assert fms.get_farmer(db_session, original_id) is not None

    def test_update_farm_applies_allowed_fields(self, db_session):
        farmer = fms.create_farmer(db_session, name="Mohan")
        farm = fms.create_farm(db_session, farmer.id, lat=1.0, lng=2.0, label="A")
        updated = fms.update_farm(db_session, farm.id, label="B", lat=3.0)
        assert updated.label == "B"
        assert updated.lat == 3.0

    def test_update_farm_ignores_farmer_id_reassignment(self, db_session):
        """The actual production bug: farmer_id could be overwritten to
        reassign a farm to a different farmer (IDOR-style)."""
        farmer_a = fms.create_farmer(db_session, name="A")
        farmer_b = fms.create_farmer(db_session, name="B")
        farm = fms.create_farm(db_session, farmer_a.id, lat=1.0, lng=2.0)

        fms.update_farm(db_session, farm.id, farmer_id=farmer_b.id)

        refreshed = fms.get_farm(db_session, farm.id)
        assert refreshed.farmer_id == farmer_a.id

    def test_update_farm_ignores_area_ha_direct_override(self, db_session):
        farmer = fms.create_farmer(db_session, name="C")
        farm = fms.create_farm(db_session, farmer.id, lat=1.0, lng=2.0)
        fms.update_farm(db_session, farm.id, area_ha=99999.0)
        assert fms.get_farm(db_session, farm.id).area_ha != 99999.0


# ---------------------------------------------------------------------------
# Score signature (credit-intelligence trust boundary)
# ---------------------------------------------------------------------------

class TestScoreSignature:
    def _import_app(self, monkeypatch):
        # app.py has heavy import-time side effects (Flask app creation,
        # DB init attempt, CORS setup) but none require live credentials —
        # only auth_service.JWT_SECRET (falls back to a dev default) is
        # actually used by the functions under test.
        import app as app_module
        return app_module

    def test_valid_signature_verifies(self, monkeypatch):
        app_module = self._import_app(monkeypatch)
        issued_at = time.time()
        sig = app_module._sign_score(750, 12.34567, 77.65432, issued_at)
        body = {
            "score": 750,
            "coordinates": {"lat": 12.34567, "lng": 77.65432},
            "_score_sig": sig,
            "_score_sig_ts": issued_at,
        }
        assert app_module._verify_score_signature(body) is True

    def test_tampered_score_fails(self, monkeypatch):
        app_module = self._import_app(monkeypatch)
        issued_at = time.time()
        sig = app_module._sign_score(750, 12.34567, 77.65432, issued_at)
        body = {
            "score": 900,  # attacker inflated the score after signing
            "coordinates": {"lat": 12.34567, "lng": 77.65432},
            "_score_sig": sig,
            "_score_sig_ts": issued_at,
        }
        assert app_module._verify_score_signature(body) is False

    def test_missing_signature_fails(self, monkeypatch):
        app_module = self._import_app(monkeypatch)
        body = {"score": 750, "coordinates": {"lat": 12.0, "lng": 77.0}}
        assert app_module._verify_score_signature(body) is False

    def test_stale_signature_fails(self, monkeypatch):
        app_module = self._import_app(monkeypatch)
        monkeypatch.setattr(app_module, "SCORE_SIGNATURE_MAX_AGE_S", 60)
        issued_at = time.time() - 3600  # 1 hour old, budget is 60s
        sig = app_module._sign_score(750, 12.0, 77.0, issued_at)
        body = {
            "score": 750,
            "coordinates": {"lat": 12.0, "lng": 77.0},
            "_score_sig": sig,
            "_score_sig_ts": issued_at,
        }
        assert app_module._verify_score_signature(body) is False

    def test_tampered_coordinates_fail(self, monkeypatch):
        app_module = self._import_app(monkeypatch)
        issued_at = time.time()
        sig = app_module._sign_score(750, 12.0, 77.0, issued_at)
        body = {
            "score": 750,
            "coordinates": {"lat": 13.0, "lng": 77.0},  # moved after signing
            "_score_sig": sig,
            "_score_sig_ts": issued_at,
        }
        assert app_module._verify_score_signature(body) is False


# ---------------------------------------------------------------------------
# SSRF: satellite-thumbnail host allowlist
# ---------------------------------------------------------------------------

class TestSafeImageHostAllowlist:
    def test_rejects_non_googleapis_host_without_network_call(self, monkeypatch):
        called = []
        monkeypatch.setattr(pdf_report.requests, "get", lambda *a, **k: called.append(1))
        result = pdf_report._safe_image("http://169.254.169.254/latest/meta-data/", 174)
        assert result is None
        assert not called  # never even attempted the request

    def test_rejects_http_scheme_on_allowed_host(self, monkeypatch):
        called = []
        monkeypatch.setattr(pdf_report.requests, "get", lambda *a, **k: called.append(1))
        result = pdf_report._safe_image("http://earthengine.googleapis.com/thumb.png", 174)
        assert result is None
        assert not called

    def test_rejects_lookalike_host(self, monkeypatch):
        """A host merely containing "googleapis.com" as a substring (not
        as its actual domain suffix) must not slip through."""
        called = []
        monkeypatch.setattr(pdf_report.requests, "get", lambda *a, **k: called.append(1))
        result = pdf_report._safe_image("https://googleapis.com.evil.example/x.png", 174)
        assert result is None
        assert not called

    def test_accepts_googleapis_host(self, monkeypatch):
        import io
        from PIL import Image as PILImage

        buf = io.BytesIO()
        PILImage.new("RGB", (10, 10)).save(buf, format="PNG")
        png_bytes = buf.getvalue()

        class FakeResp:
            content = png_bytes
            def raise_for_status(self):
                pass

        monkeypatch.setattr(pdf_report.requests, "get", lambda *a, **k: FakeResp())
        result = pdf_report._safe_image("https://earthengine.googleapis.com/v1/thumb.png", 174)
        assert result is not None
