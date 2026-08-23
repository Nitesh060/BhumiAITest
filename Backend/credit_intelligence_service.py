"""
credit_intelligence_service.py
================================
Phase 6 — BCIS Credit Intelligence.

HONESTY NOTE: This is a transparent, documented WEIGHTED FORMULA, not
a trained meta-model. Bhumi's own vision doc describes BCIS as a
model trained on 5 AI signals + real repayment history — this app has
no repayment/NPA history to train on yet. Every weight below is
disclosed in the response so a credit officer can see exactly why a
score came out the way it did (matching the "AI Model Governance"
principle in the Bhumi doc itself — no black-box credit decision).

Formula
-------
BCIS risk score (0-100, HIGHER = MORE risk stress, opposite direction
from FarmScore) combines:
  - FarmScore inverted (40%)      — land/satellite quality signal
  - Climate risk level (20%)      — rule-based rainfall/temp flags
  - Flood risk level (20%)        — JRC+slope+SAR composite
  - Drought history (20%)         — CHIRPS-derived drought years in
                                     the last 10 years, from the
                                     drought_instances data

Bands (matching the GREEN/AMBER/RED convention in the Bhumi doc):
  GREEN  0-40   -> standard loan ceiling
  AMBER  41-65  -> reduced ceiling (60% instead of 70%), RM review flagged
  RED    66-100 -> AUTO-FREEZE, credit officer must manually release

Loan ceiling formula (from the Bhumi doc, implemented literally):
  1. yield lower bound (kg/ha) x farm area (ha) = total yield lower bound
  2. total yield x price/quintal = income estimate
  3. loan ceiling = income estimate x 70% (GREEN) or 60% (AMBER), 0 (RED)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Fallback indicative crop prices (Rs/quintal) — used ONLY when live MSP
# (govt_data_service.fetch_mandi_price, needs DATA_GOV_IN_KEY) isn't
# configured or returns unavailable. These are rough, dated reference
# points, NOT live market prices — always prefer the live MSP call when
# available, and treat this as a last-resort estimate only.
FALLBACK_PRICE_RS_PER_QUINTAL = {
    "Rice": 2100,
    "Wheat": 2275,
    "Maize": 2090,
    "Groundnut": 6377,
}

RISK_LEVEL_TO_SCORE = {"Low": 10, "Moderate": 50, "High": 90}


def estimate_income(yield_prediction: Optional[Dict[str, Any]], live_price: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Combines the yield estimate (see yield_prediction.py, Phase
    earlier) with a price per quintal to get a season income estimate.
    Uses live MSP if the caller already fetched one (via
    govt_data_service.fetch_mandi_price), else falls back to the
    static indicative table above.
    """
    if not yield_prediction or yield_prediction.get("estimated_total_yield_quintal") is None:
        return {
            "available": False,
            "reason": "No total yield estimate available — a farm boundary (polygon) is needed to compute total tonnage, not just per-hectare yield.",
        }

    crop = yield_prediction["crop"]
    total_quintal = yield_prediction["estimated_total_yield_quintal"]

    price_source = "fallback_indicative"
    price = FALLBACK_PRICE_RS_PER_QUINTAL.get(crop)
    if live_price and live_price.get("available") and live_price.get("modal_price_rs_per_quintal"):
        price = float(live_price["modal_price_rs_per_quintal"])
        price_source = "live_mandi_price"

    if price is None:
        return {"available": False, "reason": f"No price reference (live or fallback) for crop '{crop}'."}

    income_estimate_rs = round(total_quintal * price)

    return {
        "available": True,
        "crop": crop,
        "total_yield_quintal": total_quintal,
        "price_rs_per_quintal": price,
        "price_source": price_source,
        "income_estimate_rs": income_estimate_rs,
        "note": "Formula-based estimate (yield proxy x price), not a measured/audited income figure." +
                (" Price is a static indicative fallback, not live MSP — configure DATA_GOV_IN_KEY for live prices." if price_source == "fallback_indicative" else ""),
    }


def compute_bcis_score(farmscore: Optional[int], climate_risk_level: Optional[str],
                        flood_risk_level: Optional[str], drought_years: Optional[list]) -> Dict[str, Any]:
    """Returns {"score": 0-100, "tier": "GREEN"|"AMBER"|"RED", "drivers": [...]}."""
    drivers = []
    components = {}

    # FarmScore inverted: 400-1000 scale -> 0-100 risk (higher FarmScore = lower risk)
    if farmscore is not None:
        farmscore_risk = round((1000 - farmscore) / (1000 - 400) * 100)
        farmscore_risk = max(0, min(100, farmscore_risk))
    else:
        farmscore_risk = 50  # neutral if unknown
        drivers.append("FarmScore unavailable — used neutral default")
    components["farmscore_risk"] = farmscore_risk

    climate_score = RISK_LEVEL_TO_SCORE.get(climate_risk_level, 50)
    components["climate_risk"] = climate_score
    if climate_risk_level in ("Moderate", "High"):
        drivers.append(f"Climate risk: {climate_risk_level}")

    flood_score = RISK_LEVEL_TO_SCORE.get(flood_risk_level, 10)
    components["flood_risk"] = flood_score
    if flood_risk_level in ("Moderate", "High"):
        drivers.append(f"Flood risk: {flood_risk_level}")

    recent_drought_count = len([y for y in (drought_years or []) if y >= 2016])  # last ~10 years
    drought_score = min(100, recent_drought_count * 25)
    components["drought_history"] = drought_score
    if recent_drought_count >= 2:
        drivers.append(f"{recent_drought_count} drought years in the last decade")

    weighted = round(
        farmscore_risk * 0.40 + climate_score * 0.20 + flood_score * 0.20 + drought_score * 0.20
    )

    if weighted <= 40:
        tier = "GREEN"
    elif weighted <= 65:
        tier = "AMBER"
    else:
        tier = "RED"

    if not drivers:
        drivers.append("No elevated risk factors identified")

    return {
        "score": weighted,
        "tier": tier,
        "components": components,
        "weights": {"farmscore_risk": "40%", "climate_risk": "20%", "flood_risk": "20%", "drought_history": "20%"},
        "drivers": drivers,
        "method": "Transparent weighted formula — not a trained ML meta-model (no repayment-history training data available yet).",
    }


def recommend_loan_ceiling(income_estimate: Optional[Dict[str, Any]], bcis: Dict[str, Any],
                            policy_max_rs: Optional[float] = None) -> Dict[str, Any]:
    """Implements the Bhumi doc's loan-ceiling formula literally:
    ceiling = income_estimate x 70% (GREEN) or 60% (AMBER), 0 for RED
    (auto-freeze — see auto_freeze_check). Capped at policy_max_rs if
    the caller supplies one (Annapurna's own per-crop/per-acre policy
    cap), whichever is lower.
    """
    tier = bcis["tier"]

    if tier == "RED":
        return {
            "loan_ceiling_rs": 0,
            "tier": tier,
            "reason": "AUTO-FROZEN — high repayment stress score. Requires credit officer manual release (see auto_freeze_check).",
        }

    if not income_estimate or not income_estimate.get("available"):
        return {
            "loan_ceiling_rs": None,
            "tier": tier,
            "reason": "Cannot compute a loan ceiling without an income estimate (needs a farm boundary + yield estimate).",
        }

    pct = 0.70 if tier == "GREEN" else 0.60
    ceiling = round(income_estimate["income_estimate_rs"] * pct)

    if policy_max_rs is not None:
        ceiling = min(ceiling, policy_max_rs)

    return {
        "loan_ceiling_rs": ceiling,
        "tier": tier,
        "percent_of_income": f"{int(pct*100)}%",
        "income_estimate_rs": income_estimate["income_estimate_rs"],
        "capped_by_policy": policy_max_rs is not None and ceiling == policy_max_rs,
        "note": "Ceiling = income estimate x tier percentage. Both the income and the tier are formula-based estimates — treat this as a starting reference for the credit officer, not an automatic disbursement amount.",
    }


def auto_freeze_check(bcis: Dict[str, Any]) -> Dict[str, Any]:
    frozen = bcis["tier"] == "RED"
    return {
        "frozen": frozen,
        "tier": bcis["tier"],
        "reason": f"BCIS risk score {bcis['score']}/100 (RED band, >65) — " + "; ".join(bcis["drivers"]) if frozen else None,
        "action_required": "Credit officer must manually review and release before disbursement." if frozen else None,
    }
