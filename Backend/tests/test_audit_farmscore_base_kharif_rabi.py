"""Regression tests for merging the two separate scores into one:

Previously the app computed TWO numbers — a flat, season-blind
20-parameter FarmScore (300-900) AND a separate "Bhumi Seasonal Score"
(Base+Kharif+Rabi, 0-1000) shown alongside it. Per an explicit product
decision, there is now only ONE FarmScore: Base (0-200, irrigation +
cropping intensity) + Average Kharif Score (0-400) + Average Rabi Score
(0-400), each of the latter two computed with the same transparent
20-parameter suitability formula scoped to that season's own values,
rescaled to a 400-1000 final score with SatSure's exact 5-tier grade
bands (Poor 400-625, Fair 626-725, Good 726-790, Very Good 791-870,
Excellent 871-1000).
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
TWICE_A_YEAR = {"label": "Twice a Year"}


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
    def test_full_data_scores_within_400_1000(self):
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        assert 400 <= result["final_score"] <= 1000

    def test_completely_missing_data_floors_at_400(self):
        result = sss.compute_farmscore(None, None, {}, {})
        assert result["final_score"] == 400
        assert result["grade"] == "Poor"

    def test_partial_availability_still_scales_to_full_range(self):
        """Losing the Rabi component shouldn't cap the score at some
        fraction of 1000 — it should rescale against the max actually
        achieved (Base 200 + Kharif 400 = 600), same philosophy as the
        old Bhumi Seasonal Score's partial-data handling."""
        full = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        no_rabi = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, {})
        assert 400 <= no_rabi["final_score"] <= 1000
        # Both are strong (irrigated, twice-a-year, healthy NDVI) — should
        # land in a similar grade even with one seasonal leg missing.
        assert abs(full["final_score"] - no_rabi["final_score"]) < 50


class TestGradeBandsMatchReferenceReport:
    @pytest.mark.parametrize("score,expected", [
        (400, "Poor"), (625, "Poor"), (626, "Fair"), (725, "Fair"),
        (726, "Good"), (790, "Good"), (791, "Very Good"), (870, "Very Good"),
        (871, "Excellent"), (1000, "Excellent"),
    ])
    def test_bands(self, score, expected):
        assert assign_grade(score) == expected


class TestBaseKharifRabiComposition:
    def test_base_score_is_irrigation_and_cropping_intensity_only(self):
        base = sss.compute_base_score(IRRIGATED, TWICE_A_YEAR)
        assert base["score"] == 200  # both signals maxed out
        assert base["max_score"] == 200
        assert base["data_available"] is True

    def test_base_score_unavailable_when_no_signal(self):
        base = sss.compute_base_score(None, None)
        assert base["score"] is None
        assert base["data_available"] is False

    def test_kharif_and_rabi_use_the_same_20_parameter_formula(self):
        """Kharif/Rabi sub-scores are not a NDVI-only heuristic anymore —
        they're the full comprehensive formula, scoped per season."""
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        breakdown = result["breakdown"]
        assert breakdown["kharif"]["parameters_used"] == 20
        assert breakdown["rabi"]["parameters_used"] == 20
        assert breakdown["kharif"]["max_score"] == 400
        assert breakdown["rabi"]["max_score"] == 400
        assert breakdown["base"]["max_score"] == 200

    def test_raw_total_equals_base_plus_kharif_plus_rabi(self):
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        b = result["breakdown"]
        raw_total = b["base"]["score"] + b["kharif"]["score"] + b["rabi"]["score"]
        expected_final = round(400 + (raw_total / 1000) * 600)
        assert result["final_score"] == expected_final


class TestMergedComponentsBackwardCompatible:
    """app.js/report.js/gemini_service/whatsapp_service/pdf_report all
    read components[key].raw_value / .sub_score / .weight / .label /
    .unit / .source / .data_available at the TOP level of each
    component — this must keep working even though each parameter is
    now scored twice (once per season) under the hood."""

    def test_top_level_fields_present_for_every_parameter(self):
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        ndvi = result["components"]["ndvi"]
        for field in ("raw_value", "sub_score", "weight", "label", "unit", "source", "data_available"):
            assert field in ndvi

    def test_kharif_and_rabi_breakdown_nested_under_each_component(self):
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        ndvi = result["components"]["ndvi"]
        assert ndvi["kharif"]["raw_value"] == 0.6
        assert ndvi["rabi"]["raw_value"] == 0.5
        assert ndvi["raw_value"] == 0.55  # averaged, backward-compatible

    def test_parameters_used_counts_distinct_parameters_not_season_pairs(self):
        result = sss.compute_farmscore(IRRIGATED, TWICE_A_YEAR, FULL_KHARIF_RAW, FULL_RABI_RAW)
        assert result["parameters_used"] == 20
        assert result["parameters_total"] == 20
