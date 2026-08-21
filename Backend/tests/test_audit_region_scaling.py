"""Regression tests for the same unscaled-region bug (already found and
fixed in seasonal_data_service.py / weather_indices_service.py) recurring
in weather_soil_terrain_service.py and enrichment_service.py, plus the
hardcoded-year bug in crop_intelligence_service.py.

Mocked-EE only — no live Earth Engine backend is touched.
"""
import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import weather_soil_terrain_service as wsts
import enrichment_service as es
import crop_intelligence_service as cis


def _chainable_ee_stub():
    stub = MagicMock()
    chain = MagicMock()
    chain.filterDate.return_value = chain
    chain.filterBounds.return_value = chain
    chain.select.return_value = chain
    chain.mean.return_value = chain
    chain.sum.return_value = chain
    chain.map.return_value = chain
    chain.multiply.return_value = chain
    chain.subtract.return_value = chain
    chain.reduce.return_value = chain
    chain.combine.return_value = chain
    stub.ImageCollection.return_value = chain
    stub.Image.return_value = chain
    return stub, chain


class TestHistoricalWeatherUsesScaledRegion:
    def test_uses_chirps_and_modis_scale(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(wsts, "ee", stub)
        scale_calls = []
        monkeypatch.setattr(wsts, "_scaled_region", lambda lat, lng, polygon, scale_m, **k: scale_calls.append(scale_m) or MagicMock())
        monkeypatch.setattr(wsts, "_reduce_mean_with_retry", lambda *a, **k: 100.0)

        result = wsts.fetch_historical_weather(12.0, 77.0, None, start_year=2020, end_year=2021)

        assert wsts.CHIRPS_SCALE_M in scale_calls
        assert wsts.MODIS_LST_SCALE_M in scale_calls
        assert all(y["total_rainfall_mm"] == 100.0 for y in result["yearly"])
        assert all(y["avg_temperature_c"] == 100.0 for y in result["yearly"])

    def test_never_reduces_against_bare_get_region(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(wsts, "ee", stub)
        get_region_calls = []
        monkeypatch.setattr(wsts, "_get_region", lambda *a, **k: get_region_calls.append(1) or MagicMock())
        monkeypatch.setattr(wsts, "_scaled_region", lambda *a, **k: MagicMock())
        monkeypatch.setattr(wsts, "_reduce_mean_with_retry", lambda *a, **k: 5.0)

        wsts.fetch_historical_weather(12.0, 77.0, None, start_year=2020, end_year=2020)
        assert not get_region_calls


class TestSoilMoistureUsesScaledRegion:
    def test_uses_smap_scale(self, monkeypatch):
        stub, chain = _chainable_ee_stub()
        chain.size.return_value.getInfo.return_value = 3
        monkeypatch.setattr(wsts, "ee", stub)
        scale_calls = []
        monkeypatch.setattr(wsts, "_scaled_region", lambda lat, lng, polygon, scale_m, **k: scale_calls.append(scale_m) or MagicMock())
        monkeypatch.setattr(wsts, "_reduce_mean_with_retry", lambda *a, **k: 0.22)

        result = wsts.fetch_soil_moisture(12.0, 77.0, None)

        assert wsts.SMAP_SCALE_M in scale_calls
        assert result["available"] is True
        assert result["surface_soil_moisture_m3_m3"] == 0.22


class TestTemperatureAnnualRangeUsesScaledRegion:
    def test_uses_modis_scale_not_bare_region(self, monkeypatch):
        stub, chain = _chainable_ee_stub()
        chain.reduceRegion.return_value.getInfo.return_value = {
            "LST_C_min": 18.0, "LST_C_max": 40.0, "LST_C_mean": 29.0,
        }
        monkeypatch.setattr(es, "ee", stub)
        get_region_calls = []
        scale_calls = []
        monkeypatch.setattr(es, "_get_region", lambda *a, **k: get_region_calls.append(1) or MagicMock())
        monkeypatch.setattr(es, "_scaled_region", lambda lat, lng, polygon, scale_m, **k: scale_calls.append(scale_m) or MagicMock())

        result = es.fetch_temperature_annual_range(12.0, 77.0, None)

        assert not get_region_calls
        assert es.MODIS_LST_SCALE_M in scale_calls
        assert result == {"min_c": 18.0, "max_c": 40.0, "mean_c": 29.0, "source": "MODIS LST (full calendar year 2023)"}

    def test_exception_is_logged_not_silent(self, monkeypatch, caplog):
        stub, chain = _chainable_ee_stub()
        chain.reduceRegion.side_effect = RuntimeError("boom")
        monkeypatch.setattr(es, "ee", stub)
        monkeypatch.setattr(es, "_scaled_region", lambda *a, **k: MagicMock())
        import logging
        with caplog.at_level(logging.ERROR):
            result = es.fetch_temperature_annual_range(12.0, 77.0, None)
        assert result["min_c"] is None
        assert any("Temperature annual-range fetch failed" in r.message for r in caplog.records)


class TestSilentExceptionsNowLogged:
    def test_irrigation_signal_logs_on_failure(self, monkeypatch, caplog):
        stub, chain = _chainable_ee_stub()
        chain.map.side_effect = RuntimeError("boom")
        monkeypatch.setattr(es, "ee", stub)
        import logging
        monkeypatch.setattr(es, "_get_region", lambda *a, **k: MagicMock())
        with caplog.at_level(logging.ERROR):
            result = es.fetch_irrigation_signal(12.0, 77.0, None)
        assert result["dry_season_ndvi"] is None
        assert any("Irrigation-signal fetch failed" in r.message for r in caplog.records)

    def test_prosperity_proxy_logs_on_failure(self, monkeypatch, caplog):
        stub, chain = _chainable_ee_stub()
        monkeypatch.setattr(es, "ee", stub)
        monkeypatch.setattr(es, "_buffered_region", lambda *a, **k: MagicMock())
        monkeypatch.setattr(es, "_reduce_mean", MagicMock(side_effect=RuntimeError("boom")))
        import logging
        with caplog.at_level(logging.ERROR):
            result = es.fetch_prosperity_proxy(12.0, 77.0, None)
        assert result["avg_radiance"] is None
        assert any("Prosperity-proxy fetch failed" in r.message for r in caplog.records)

    def test_nearest_water_body_logs_on_failure(self, monkeypatch, caplog):
        stub, chain = _chainable_ee_stub()
        monkeypatch.setattr(es, "ee", stub)
        monkeypatch.setattr(es, "_buffered_region", lambda *a, **k: MagicMock())
        stub.Image.return_value.select.return_value.gt.return_value.selfMask.return_value.reduceRegion.side_effect = RuntimeError("boom")
        import logging
        with caplog.at_level(logging.ERROR):
            result = es.fetch_nearest_water_body_signal(12.0, 77.0, None)
        assert result["water_pixels_within_2km"] == 0
        assert any("Nearest-water-body fetch failed" in r.message for r in caplog.records)

    def test_village_population_logs_on_failure(self, monkeypatch, caplog):
        stub, chain = _chainable_ee_stub()
        chain.mosaic.side_effect = RuntimeError("boom")
        monkeypatch.setattr(es, "ee", stub)
        monkeypatch.setattr(es, "_buffered_region", lambda *a, **k: MagicMock())
        import logging
        with caplog.at_level(logging.ERROR):
            result = es.fetch_village_population(12.0, 77.0)
        assert result["estimated_population"] is None
        assert any("Village-population fetch failed" in r.message for r in caplog.records)


class TestCropIntelligenceYearNoLongerHardcoded:
    def test_latest_completed_calendar_year_is_dynamic(self):
        assert cis._latest_completed_calendar_year() == datetime.utcnow().year - 1

    def test_monthly_ndvi_uses_dynamic_year_by_default(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(cis, "ee", stub)
        monkeypatch.setattr(cis, "_reduce_mean", lambda *a, **k: 0.5)
        seen_dates = []
        real_filter_date = stub.ImageCollection.return_value.filterDate

        def capture(start, end):
            seen_dates.append((start, end))
            return stub.ImageCollection.return_value
        stub.ImageCollection.return_value.filterDate = capture

        cis._fetch_monthly_ndvi(MagicMock())
        expected_year = datetime.utcnow().year - 1
        assert seen_dates[0][0] == f"{expected_year}-01-01"
        assert "2023" not in seen_dates[0][0]

    def test_flood_check_uses_dynamic_year(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(cis, "ee", stub)
        monkeypatch.setattr(cis, "_reduce_mean", lambda *a, **k: 0.1)
        seen_dates = []

        def capture(start, end):
            seen_dates.append((start, end))
            return stub.ImageCollection.return_value
        stub.ImageCollection.return_value.filterDate = capture

        cis._check_early_season_flooding(MagicMock(), kharif=True)
        expected_year = datetime.utcnow().year - 1
        assert seen_dates[0] == (f"{expected_year}-06-15", f"{expected_year}-08-15")
