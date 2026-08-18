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
