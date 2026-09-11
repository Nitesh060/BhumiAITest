from comprehensive_score_service import compute_comprehensive_score
from scoring import calculate_score


def test_missing_parameter_weights_are_redistributed():
    result = compute_comprehensive_score({"ndvi": 0.60, "ndmi": 0.30})
    assert result["score_0_100"] is not None
    assert result["parameters_used"] == 2
    assert abs(sum(c["effective_weight_pct"] for c in result["components"].values()) - 100) < 0.01


def test_air_temperature_is_zero_weighted_when_source_is_lst():
    result = compute_comprehensive_score({"air_temp": 30.0, "lst": 30.0})
    assert result["components"]["air_temp"]["weight_pct"] == 0.0
    assert result["components"]["lst"]["weight_pct"] == 10.0


def test_real_air_temperature_is_preserved_by_scoring_adapter():
    result = calculate_score({"air_temp": 28.0, "lst": 32.0})
    assert result["components"]["air_temp"]["raw_value"] == 28.0
    assert result["components"]["air_temp"]["data_available"] is True
    assert result["components"]["air_temp"]["weight"] == 5.0
    assert result["components"]["lst"]["raw_value"] == 32.0


def test_earth_engine_air_temperature_alias_is_supported():
    result = calculate_score({"air_temperature": 28.0, "lst": 32.0})
    assert result["components"]["air_temp"]["raw_value"] == 28.0


def test_score_has_explicit_confidence_for_sparse_data():
    result = compute_comprehensive_score({"ndvi": 0.60})
    assert result["confidence"] == "low"
    assert result["parameters_used"] == 1


def test_extreme_values_do_not_create_scores_above_100():
    result = compute_comprehensive_score({
        "ndvi": 99,
        "rainfall": 99,
        "lst": 99,
        "gdd": 99999,
    })
    assert 0 <= result["score_0_100"] <= 100


def test_an_ordinary_healthy_farm_does_not_saturate_the_index():
    """Regression: `_range_score` returned a flat 100.0 anywhere inside a
    parameter's ideal band, and those bands were centred on merely-adequate
    readings. An ordinary healthy Kharif paddy farm therefore maxed out 17
    of 20 parameters and scored 99/100 — the index could not separate an
    average farm from an outstanding one, so almost everything graded
    Excellent / Lowest Risk."""
    ordinary_farm = {
        "ndvi": 0.62, "evi": 0.42, "savi": 0.45, "msavi": 0.55, "ndre": 0.24,
        "ndmi": 0.30, "ndwi": -0.05, "ci_green": 2.4, "ci_rededge": 1.5,
        "vv": -9.5, "vh": -16.0, "vh_vv": 0.22, "rvi": 0.55,
        "rainfall": 6.5, "air_temp": 28.5, "solar_radiation": 17.0,
        "spi": 0.4, "spei": 0.3, "gdd": 1800, "lst": 30.0,
    }
    result = compute_comprehensive_score(ordinary_farm)
    saturated = [k for k, c in result["components"].items() if c["sub_score"] == 100.0]
    assert len(saturated) <= 3, f"{len(saturated)} parameters saturate at 100: {saturated}"
    assert result["score_0_100"] < 90


def test_grade_is_read_off_the_raw_0_1000_scale():
    """The 400-point floor is gone: a mediocre 55/100 must grade Poor, not
    the "Good" it became under `400 + score/100*600`."""
    result = compute_comprehensive_score({"ndvi": 0.35, "ndmi": 0.05})
    assert result["score_0_1000"] == round(result["score_0_100"] * 10)
    assert result["score_400_1000"] == result["score_0_1000"]  # legacy alias
