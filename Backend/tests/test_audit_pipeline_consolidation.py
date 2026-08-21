"""Regression tests for the pipeline-consolidation audit fix:

1. The seasonal-pipeline rebinding (previously hidden in wsgi.py,
   silently reassigning app.py's own globals from the outside after
   import) is now explicit in app.py itself. Confirms the wiring is
   unchanged from before (same functions bound to the same names) so
   this refactor is behavior-preserving for everything except the two
   bugs below.
2. _seasonal_farm_data used to overwrite `temperature` with the seasonal
   LST value but leave `lst` holding the stale, non-seasonal value —
   compute_farmscore reads `lst` specifically (10% of the score
   weighting). Both must now be updated together.
3. _seasonal_farm_data used to leave a stale `rainfall_reason` (set by
   the real, non-seasonal fetch) standing next to the seasonal
   rainfall value it overwrote — producing a None value with a stale
   "no error" reason. The reason must now reflect the value actually
   kept.
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module


class TestSeasonalPipelineWiring:
    def test_rebound_functions_point_to_seasonal_wrappers(self):
        assert app_module.fetch_farm_data is app_module._seasonal_farm_data
        assert app_module.fetch_extended_indices is app_module._seasonal_extended_indices
        assert app_module.calculate_spectral_intelligence is app_module._seasonal_spectral
        assert app_module._fetch_sar_for_flood is app_module._seasonal_sar
        assert app_module.fetch_solar_radiation is app_module._seasonal_solar
        assert app_module.fetch_spi is app_module._seasonal_spi
        assert app_module.fetch_gdd is app_module._seasonal_gdd

    def test_spei_deliberately_not_rebound(self):
        """SPEI keeps using the real weather_indices_service.fetch_spei
        (CSIC + Thornthwaite fallback) — deliberately, not a leftover
        naming-typo escape like before."""
        assert app_module.fetch_spei.__module__ == "weather_indices_service"
        assert app_module.fetch_spei.__name__ == "fetch_spei"

    def test_original_fetch_farm_data_is_the_real_one(self):
        assert app_module._original_fetch_farm_data.__module__ == "earth_engine_service"


class TestSeasonalFarmDataLstFix:
    def test_temperature_and_lst_both_get_seasonal_value(self, monkeypatch):
        monkeypatch.setattr(
            app_module, "_original_fetch_farm_data",
            lambda lat, lng, polygon: {"temperature": 999.0, "lst": 999.0, "rainfall": 5.0, "rainfall_reason": None},
        )
        monkeypatch.setattr(
            app_module, "_get_seasonal",
            lambda lat, lng, polygon=None: {
                "raw_values": {"ndvi": 0.5, "ndmi": 0.2, "ndwi": 0.1, "rainfall": 6.0, "lst": 31.5},
                "seasons": {}, "season_method": "test", "season_count_used": 2,
            },
        )
        result = app_module._seasonal_farm_data(12.0, 77.0, None)
        assert result["temperature"] == 31.5
        assert result["lst"] == 31.5  # previously stayed at the stale 999.0

    def test_lst_none_when_seasonal_lst_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            app_module, "_original_fetch_farm_data",
            lambda lat, lng, polygon: {"temperature": 25.0, "lst": 25.0, "rainfall": 5.0, "rainfall_reason": None},
        )
        monkeypatch.setattr(
            app_module, "_get_seasonal",
            lambda lat, lng, polygon=None: {
                "raw_values": {"lst": None, "rainfall": 5.0},
                "seasons": {}, "season_method": "test", "season_count_used": 0,
            },
        )
        result = app_module._seasonal_farm_data(12.0, 77.0, None)
        assert result["temperature"] is None
        assert result["lst"] is None


class TestSeasonalFarmDataRainfallReasonFix:
    def test_reason_cleared_when_seasonal_rainfall_succeeds(self, monkeypatch):
        """The real (non-seasonal) fetch failed and set a reason, but the
        seasonal rainfall we actually keep succeeded — the stale failure
        reason must not survive next to a real value."""
        monkeypatch.setattr(
            app_module, "_original_fetch_farm_data",
            lambda lat, lng, polygon: {"temperature": 25.0, "lst": 25.0, "rainfall": None,
                                        "rainfall_reason": "No individual month in the Jun-Oct window returned a usable CHIRPS value."},
        )
        monkeypatch.setattr(
            app_module, "_get_seasonal",
            lambda lat, lng, polygon=None: {
                "raw_values": {"rainfall": 7.2, "lst": 25.0},
                "seasons": {}, "season_method": "test", "season_count_used": 2,
            },
        )
        result = app_module._seasonal_farm_data(12.0, 77.0, None)
        assert result["rainfall"] == 7.2
        assert result["rainfall_reason"] is None

    def test_reason_set_when_seasonal_rainfall_fails_despite_real_success(self, monkeypatch):
        """This is the exact bug seen live: the real fetch succeeded
        (reason correctly None) but the seasonal rainfall we overwrite
        it with failed — the None/None combo made "why is this null"
        undebuggable. A reason must be attached now."""
        monkeypatch.setattr(
            app_module, "_original_fetch_farm_data",
            lambda lat, lng, polygon: {"temperature": 25.0, "lst": 25.0, "rainfall": 8.5, "rainfall_reason": None},
        )
        monkeypatch.setattr(
            app_module, "_get_seasonal",
            lambda lat, lng, polygon=None: {
                "raw_values": {"rainfall": None, "lst": 25.0},
                "seasons": {}, "season_method": "test", "season_count_used": 2,
            },
        )
        result = app_module._seasonal_farm_data(12.0, 77.0, None)
        assert result["rainfall"] is None
        assert result["rainfall_reason"] is not None
        assert "seasonal" in result["rainfall_reason"].lower()
