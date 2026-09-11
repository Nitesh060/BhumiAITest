"""Regression test for the real live-dashboard bug: seasonal_data_service.py
(the module wsgi.py actually routes Rainfall/SPI/Solar Radiation/GDD/LST
through on the deployed app) computed its CHIRPS/ERA5/MODIS reductions
against the bare `_get_region()` geometry — a ~30m point buffer, or a
small farm polygon — instead of a region scaled to each dataset's own
pixel size (`_scaled_region`). Earth Engine's weighted mean reducer only
counts a pixel if the query geometry covers at least ~0.4% of that
pixel's area, so a 30m buffer against CHIRPS's ~5.5km pixel or
ERA5-Land's ~11km pixel covers a negligible fraction of a percent —
rainfall and solar radiation reduced to None on essentially every
request, while MODIS's ~1km pixel (temperature/GDD) happened to clear
that floor.

This test never touches a real Earth Engine backend — it mocks
`_scaled_region` and `_reduce_mean_with_retry` and asserts they are what
seasonal_data_service actually calls for the CHIRPS/ERA5/MODIS-derived
fields, with the correct per-dataset scale constant.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seasonal_data_service as sds


def _chainable_ee_stub():
    stub = MagicMock()
    chain = MagicMock()
    chain.filterDate.return_value = chain
    chain.filterBounds.return_value = chain
    chain.select.return_value = chain
    chain.mean.return_value = chain
    chain.sum.return_value = chain
    chain.multiply.return_value = chain
    chain.subtract.return_value = chain
    chain.map.return_value = chain
    stub.ImageCollection.return_value = chain
    return stub, chain


class TestSeasonWeatherUsesScaledRegion:
    def test_uses_scaled_region_not_bare_get_region(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(sds, "ee", stub)

        scaled_region_calls = []

        def fake_scaled_region(lat, lng, polygon, scale_m, extra_buffer_m=0.0):
            scaled_region_calls.append(scale_m)
            return MagicMock()

        reduce_calls = []

        def fake_reduce(image, lat, lng, polygon, scale, max_retries=3):
            reduce_calls.append(scale)
            return 42.0

        monkeypatch.setattr(sds, "_scaled_region", fake_scaled_region)
        monkeypatch.setattr(sds, "_reduce_mean_with_retry", fake_reduce)

        result = sds._season_weather(12.0, 77.0, None, "2025-06-01", "2025-11-01")

        # All three datasets must have been reduced with a region scaled
        # to THEIR OWN pixel size, not left at a Sentinel-scale default.
        assert sds.CHIRPS_SCALE_M in scaled_region_calls
        assert sds.MODIS_LST_SCALE_M in scaled_region_calls
        assert sds.ERA5_LAND_SCALE_M in scaled_region_calls
        assert sds.CHIRPS_SCALE_M in reduce_calls
        assert sds.MODIS_LST_SCALE_M in reduce_calls
        assert sds.ERA5_LAND_SCALE_M in reduce_calls

        assert result["rainfall"] == 42.0
        assert result["lst"] == 42.0
        assert result["gdd"] == 42.0
        assert result["solar_radiation"] == 42.0 / 1_000_000

    def test_never_reduces_against_bare_get_region_output(self, monkeypatch):
        """The old bug: reduceRegion ran against _get_region()'s tiny
        buffer directly. Confirm _get_region is not even called by
        _season_weather any more (region construction is fully delegated
        to _scaled_region, sized per dataset)."""
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(sds, "ee", stub)
        get_region_calls = []
        monkeypatch.setattr(sds, "_get_region", lambda *a, **k: get_region_calls.append(1) or MagicMock())
        monkeypatch.setattr(sds, "_scaled_region", lambda *a, **k: MagicMock())
        monkeypatch.setattr(sds, "_reduce_mean_with_retry", lambda *a, **k: 10.0)

        sds._season_weather(12.0, 77.0, None, "2025-06-01", "2025-11-01")
        assert not get_region_calls


class TestSeasonSpiSpeiUseScaledRegion:
    def test_season_spi_uses_chirps_scale(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(sds, "ee", stub)
        scale_calls = []
        monkeypatch.setattr(sds, "_scaled_region", lambda lat, lng, polygon, scale_m, **k: scale_calls.append(scale_m) or MagicMock())
        monkeypatch.setattr(sds, "_reduce_mean_with_retry", lambda *a, **k: 100.0)

        # 6 candidate years (history_years=5 + current), all succeed -> not None
        result = sds._season_spi(12.0, 77.0, None, "2025-06-01", "2025-11-01", history_years=5)
        assert scale_calls and all(s == sds.CHIRPS_SCALE_M for s in scale_calls)
        # all identical totals -> zero variance -> SPI undefined (None), but
        # the point of this test is the region scale used, not the SPI math
        assert result is None  # stddev == 0 with identical mocked values

    def test_season_spei_uses_chirps_and_modis_scale(self, monkeypatch):
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(sds, "ee", stub)
        scale_calls = []
        monkeypatch.setattr(sds, "_scaled_region", lambda lat, lng, polygon, scale_m, **k: scale_calls.append(scale_m) or MagicMock())

        def fake_reduce(image, lat, lng, polygon, scale, max_retries=3):
            return 50.0

        monkeypatch.setattr(sds, "_reduce_mean_with_retry", fake_reduce)
        sds._season_spei(12.0, 77.0, None, "2025-06-01", "2025-11-01", history_years=5)
        assert sds.CHIRPS_SCALE_M in scale_calls
        assert sds.MODIS_LST_SCALE_M in scale_calls


class TestFetchSeasonalComprehensiveData:
    def test_optical_and_sar_still_use_plain_region(self, monkeypatch):
        """Sentinel-1/2 never had the pixel-coverage bug (10-20m native
        pixels) so they should keep using the plain _get_region — this
        guards against accidentally "fixing" something that wasn't broken."""
        stub, _ = _chainable_ee_stub()
        monkeypatch.setattr(sds, "ee", stub)
        monkeypatch.setattr(sds, "initialise_earth_engine", lambda: None)
        plain_region = MagicMock()
        monkeypatch.setattr(sds, "_get_region", lambda *a, **k: plain_region)
        monkeypatch.setattr(sds, "_scaled_region", lambda *a, **k: MagicMock())
        monkeypatch.setattr(sds, "_reduce_mean_with_retry", lambda *a, **k: 1.0)

        optical_regions = []
        sar_regions = []
        monkeypatch.setattr(sds, "_season_optical", lambda region, start, end: optical_regions.append(region) or {})
        monkeypatch.setattr(sds, "_season_sar", lambda region, start, end: sar_regions.append(region) or {})

        sds.fetch_seasonal_comprehensive_data(12.0, 77.0, None)
        assert all(r is plain_region for r in optical_regions)
        assert all(r is plain_region for r in sar_regions)


class TestSeasonWindowsAreBounded:
    """Regression tests for `latest_season_windows()`.

    Two window bugs shipped together: the Kharif fallback branch built its
    end date from the CURRENT year while starting from the PREVIOUS year, and
    the Rabi branch never clamped its end to today.
    """

    def test_kharif_window_never_spans_more_than_one_season(self):
        from datetime import date
        from seasonal_data_service import latest_season_windows
        for day in (date(2026, 1, 20), date(2026, 3, 15), date(2026, 5, 31)):
            window = latest_season_windows(day)["kharif"]
            start = date.fromisoformat(window["start"])
            end = date.fromisoformat(window["end"])
            # 1 Jun -> 1 Nov is 153 days. `date(y, 11, 1)` instead of
            # `date(k_year, 11, 1)` made this 518 days, averaging two Kharif
            # seasons and the Rabi between them into one "season".
            assert (end - start).days <= 160, f"{day}: {start} -> {end}"

    def test_no_window_extends_into_the_future(self):
        from datetime import date, timedelta
        from seasonal_data_service import latest_season_windows
        for day in (date(2026, 3, 15), date(2026, 9, 9), date(2026, 12, 15), date(2027, 1, 20)):
            for season, window in latest_season_windows(day).items():
                end = date.fromisoformat(window["end"])
                assert end <= day + timedelta(days=1), f"{day}: {season} ends {end}"

    def test_a_just_started_rabi_is_not_reported_as_complete(self):
        from datetime import date
        from seasonal_data_service import latest_season_windows
        # Six weeks into Rabi 2026-27.
        window = latest_season_windows(date(2026, 12, 15))["rabi"]
        assert window["complete"] is False

    def test_a_finished_season_is_reported_as_complete(self):
        from datetime import date
        from seasonal_data_service import latest_season_windows
        assert latest_season_windows(date(2026, 9, 9))["rabi"]["complete"] is True
        assert latest_season_windows(date(2026, 12, 15))["kharif"]["complete"] is True
