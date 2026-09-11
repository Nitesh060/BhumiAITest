"""Regression tests for input guards and time-frozen constants."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from credit_intelligence_service import DROUGHT_LOOKBACK_YEARS, compute_bcis_score
from insurance_intelligence_service import detect_fraud_signals, verify_acreage


class TestAcreageInputGuards:
    """A zero declared area used to produce available=True with
    discrepancy_pct=None, which made `match` False, which made
    detect_fraud_signals add 35 points and log
    "Acreage Under-declared (None% discrepancy)". A data-entry slip became a
    fraud flag against the claimant."""

    def test_zero_declared_area_is_rejected_not_flagged(self):
        result = verify_acreage(0, 1.9)
        assert result["available"] is False
        assert detect_fraud_signals(result, None, None)["fraud_risk_score"] == 0

    def test_negative_declared_area_is_rejected(self):
        assert verify_acreage(-1, 1.9)["available"] is False

    def test_negative_measured_area_is_rejected(self):
        assert verify_acreage(2.0, -1)["available"] is False

    def test_a_real_discrepancy_is_still_flagged(self):
        result = verify_acreage(5.0, 2.0)
        assert result["available"] is True and result["match"] is False
        assert result["flag"] == "Over-declared"
        assert detect_fraud_signals(result, None, None)["fraud_risk_score"] == 35

    def test_within_tolerance_is_a_match(self):
        assert verify_acreage(2.0, 1.95)["match"] is True


class TestCreditModelHasNoFrozenConstants:
    def test_drought_window_rolls_with_the_calendar(self):
        cutoff = date.today().year - DROUGHT_LOOKBACK_YEARS
        just_inside = compute_bcis_score(700, "Low", "Low", [cutoff])
        just_outside = compute_bcis_score(700, "Low", "Low", [cutoff - 1])
        assert just_inside["components"]["drought_history"] > just_outside["components"]["drought_history"]

    def test_farmscore_inverts_across_the_full_0_1000_range(self):
        """The inversion divided by (1000-400), assuming a floor the score no
        longer has, so every farm below 400 was clamped to the same risk."""
        assert compute_bcis_score(0, "Low", "Low", [])["components"]["farmscore_risk"] == 100
        assert compute_bcis_score(1000, "Low", "Low", [])["components"]["farmscore_risk"] == 0
        assert compute_bcis_score(500, "Low", "Low", [])["components"]["farmscore_risk"] == 50
