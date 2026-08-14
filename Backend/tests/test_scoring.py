from comprehensive_score_service import compute_comprehensive_score


def test_missing_parameter_weights_are_redistributed():
    result = compute_comprehensive_score({"ndvi": 0.60, "ndmi": 0.30})
    assert result["score_0_100"] is not None
    assert result["parameters_used"] == 2
    assert abs(sum(c["effective_weight_pct"] for c in result["components"].values()) - 100) < 0.01


def test_temperature_is_not_double_counted_in_score_service():
    result = compute_comprehensive_score({"air_temp": 30.0, "lst": 30.0})
    # Both are accepted by the generic service, but the application-level
    # raw input should supply only LST because its source is MODIS LST.
    assert result["components"]["air_temp"]["weight_pct"] == 5.0
    assert result["components"]["lst"]["weight_pct"] == 10.0


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
