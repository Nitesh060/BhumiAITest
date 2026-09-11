"""Regression tests for merging the two separate scores into one:

Previously the app computed TWO numbers — a flat, season-blind
20-parameter FarmScore (300-900) AND a separate "Bhumi Seasonal Score"
(Base+Kharif+Rabi, 0-1000) shown alongside it. Per an explicit product
decision, there is now only ONE FarmScore: Base (0-200, irrigation +
cropping intensity) + Average Kharif Score (0-400) + Average Rabi Score
(0-400), each of the latter two computed with the same transparent
20-parameter suitability formula scoped to that season's own values,
summed to a 0-1000 final score with the reference report's exact 5-tier
grade bands (Poor 0-625, Fair 626-725, Good 726-790, Very Good 791-870,
Excellent 871-1000). The bands are read off the RAW total; an earlier
version rescaled the total onto 400-1000 first and then applied these
same bands, which lifted every farm by the 400-point floor.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import seasonal_score_service as sss
from comprehensive_score_service import assign_grade

FULL_KHARIF_RAW = {
    "ndvi": 0.6, "evi": 0.4, "savi": 0.5, "msavi": 0.6, "ndre": 0.25,
    "ndmi": 0.3, "ndwi": -0.1, "ci_green": 3.0, "ci_rededge": 1.5,
    "vv": -12, "vh": -18, "vh_vv": 0.3, "rvi": 0.9,
    "rainfall": 6, "air_temp": 28, "solar_radiation": 18,
    "spi": 0.2, "spei": 0.1, "gdd": 1500, "lst": 30,
}
FULL_RABI_RAW = {
    "ndvi": 0.5, "evi": 0.35, "savi": 0.45, "msavi": 0.55, "ndre": 0.2,
    "ndmi": 0.25, "ndwi": -0.15, "ci_green": 2.5, "ci_rededge": 1.2,
    "vv": -13, "vh": -19, "vh_vv": 0.28, "rvi": 0.85,
    "rainfall": 3, "air_temp": 20, "solar_radiation": 14,
    "spi": -0.1, "spei": -0.2, "gdd": 1200, "lst": 22,
}
IRRIGATED = {"likely_irrigated": True}
TRIPLE_CROPPING = {"label": "Triple / multi cropping"}


class TestOnlyOneScoreExists:
    """The separate "Bhumi Seasonal Score" computation must be gone —
    not just unused, but no longer present at all."""

    def test_old_separate_score_function_removed(self):
        assert not hasattr(sss, "compute_seasonal_performance_score")

    def test_old_ndvi_history_functions_removed(self):
        assert not hasattr(sss, "compute_seasonal_scores_from_history")
        assert not hasattr(sss, "_season_score_from_ndvi_values")

    def test_app_wires_the_merged_farmscore_function(self):
        assert app_module.compute_farmscore_from_seasons is sss.compute_farmscore

    def test_app_no_longer_imports_a_separate_seasonal_score(self):
        assert not hasattr(app_module, "compute_seasonal_performance_score")


class TestFarmScoreRange:
    def test_full_data_scores_within_0_1000(self):
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        assert 0 <= result["final_score"] <= 1000

    def test_completely_missing_data_floors_at_zero(self):
        result = sss.compute_farmscore(None, None, {}, {})
        assert result["final_score"] == 0
        assert result["grade"] == "Poor"

    def test_losing_a_season_must_lower_the_score(self):
        """Regression: losing the Rabi leg used to drop it from BOTH the
        numerator and the denominator, so a farm that grew nothing in Rabi
        scored the same as one that grew a full second crop. A missing
        season now takes the no-data floor and stays in the denominator."""
        full = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        no_rabi = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, {})
        assert no_rabi["final_score"] < full["final_score"]
        assert no_rabi["breakdown"]["rabi"]["score"] == 200
        assert no_rabi["breakdown"]["rabi"]["data_available"] is False

    def test_a_season_holding_one_stray_parameter_is_not_scored_as_perfect(self):
        """Regression for the worst offender: app.py attached the annual
        air temperature to every season, so a season with no data at all
        arrived as {"air_temp": 28.5}. That single value normalised to ~100
        and was booked as the whole season — a clean 400/400."""
        phantom = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, {"air_temp": 28.5})
        assert phantom["breakdown"]["rabi"]["score"] == 200
        assert phantom["breakdown"]["rabi"]["scored_from"] == "no-data floor"


class TestGradeBandsMatchReferenceReport:
    @pytest.mark.parametrize("score,expected", [
        (0, "Poor"), (400, "Poor"), (625, "Poor"), (626, "Fair"), (725, "Fair"),
        (726, "Good"), (790, "Good"), (791, "Very Good"), (870, "Very Good"),
        (871, "Excellent"), (1000, "Excellent"),
    ])
    def test_bands(self, score, expected):
        assert assign_grade(score) == expected


class TestBaseKharifRabiComposition:
    def test_base_score_is_irrigation_and_cropping_intensity_only(self):
        base = sss.compute_base_score(IRRIGATED, TRIPLE_CROPPING)
        assert base["score"] == 200  # both signals maxed out
        assert base["max_score"] == 200
        assert base["data_available"] is True

    def test_base_score_unavailable_when_no_signal(self):
        base = sss.compute_base_score(None, None)
        assert base["score"] is None
        assert base["data_available"] is False


class TestCroppingIntensityLabelMatchesRealFetchFunction:
    """Regression test: compute_base_score's intensity_map previously
    listed labels ("Once a Year" / "Twice a Year" / "No Crop Grown")
    that enrichment_service.fetch_cropping_intensity() never actually
    returns — it returns "Single cropping (mono)" / "Double cropping" /
    "Triple / multi cropping". That mismatch meant intensity_map.get()
    always missed, so cropping intensity silently never contributed to
    Base Score in production, even though irrigation alone was enough
    to make data_available=True and hide the problem."""

    @pytest.mark.parametrize("label,expected_pts", [
        ("Single cropping (mono)", 40.0),
        ("Double cropping", 70.0),
        ("Triple / multi cropping", 100.0),
    ])
    def test_real_labels_are_recognized(self, label, expected_pts):
        base_with = sss.compute_base_score(None, {"label": label})
        base_without = sss.compute_base_score(None, None)
        assert base_with["data_available"] is True
        assert base_without["data_available"] is False
        # Irrigation absent in both — the only difference is the
        # cropping-intensity signal, so its points alone set base_pct.
        assert base_with["score"] == round(expected_pts / 100 * 200)

    def test_a_label_fetch_cropping_intensity_never_returns_is_ignored(self):
        """The old, made-up labels must NOT silently start matching
        again — that would just reintroduce a different mismatch."""
        base = sss.compute_base_score(None, {"label": "Twice a Year"})
        assert base["data_available"] is False
        assert base["cropping_intensity"] == "Twice a Year"  # still shown, just not scored

    def test_kharif_and_rabi_use_the_same_20_parameter_formula(self):
        """Kharif/Rabi sub-scores are not a NDVI-only heuristic anymore —
        they're the full comprehensive formula, scoped per season."""
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        breakdown = result["breakdown"]
        assert breakdown["kharif"]["parameters_used"] == 20
        assert breakdown["rabi"]["parameters_used"] == 20
        assert breakdown["kharif"]["max_score"] == 400
        assert breakdown["rabi"]["max_score"] == 400
        assert breakdown["base"]["max_score"] == 200

    def test_final_score_is_the_raw_base_plus_kharif_plus_rabi_total(self):
        """No rescale: the reported score IS Base + Kharif + Rabi."""
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        b = result["breakdown"]
        raw_total = b["base"]["score"] + b["kharif"]["score"] + b["rabi"]["score"]
        assert result["final_score"] == raw_total

    def test_season_grade_is_read_off_the_same_0_1000_bands(self):
        """237/400 projects to 592/1000 -> Poor. The old code computed
        400 + 237/400*600 = 756 and called it "Good"."""
        assert sss._grade_out_of(237, 400) == "Poor"
        assert sss._grade_out_of(278, 400) == "Fair"
        assert sss._grade_out_of(314, 400) == "Good"
        assert sss._grade_out_of(335, 400) == "Very Good"


class TestMergedComponentsBackwardCompatible:
    """app.js/report.js/gemini_service/whatsapp_service/pdf_report all
    read components[key].raw_value / .sub_score / .weight / .label /
    .unit / .source / .data_available at the TOP level of each
    component — this must keep working even though each parameter is
    now scored twice (once per season) under the hood."""

    def test_top_level_fields_present_for_every_parameter(self):
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        ndvi = result["components"]["ndvi"]
        for field in ("raw_value", "sub_score", "weight", "label", "unit", "source", "data_available"):
            assert field in ndvi

    def test_kharif_and_rabi_breakdown_nested_under_each_component(self):
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        ndvi = result["components"]["ndvi"]
        assert ndvi["kharif"]["raw_value"] == 0.6
        assert ndvi["rabi"]["raw_value"] == 0.5
        assert ndvi["raw_value"] == 0.55  # averaged, backward-compatible

    def test_parameters_used_counts_distinct_parameters_not_season_pairs(self):
        result = sss.compute_farmscore(IRRIGATED, TRIPLE_CROPPING, FULL_KHARIF_RAW, FULL_RABI_RAW)
        assert result["parameters_used"] == 20
        assert result["parameters_total"] == 20
