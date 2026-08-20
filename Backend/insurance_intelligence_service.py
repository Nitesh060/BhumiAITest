"""
insurance_intelligence_service.py
====================================
Phase 7 — Insurance Intelligence.

Mostly composes pieces already built in earlier phases:
  - Acreage Verification  -> yield_prediction.compute_polygon_area_ha (Phase 1)
  - Crop Verification     -> crop_intelligence_service.identify_crop_heuristic (Phase 3)
  - Loss Estimation        -> historical_timeline_service.fetch_before_after_comparison (Phase 2)

New in this module: the verification/fraud/claim SCORING logic that
ties those pieces together for an insurance workflow.

HONESTY NOTE: fraud "detection" here means flagging discrepancies
between declared and satellite-observed values — it does NOT mean
this app has caught real fraud. Every flag needs a human review;
treat this as a triage/prioritization tool, not an automatic
approve/reject decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Acreage Verification
# ---------------------------------------------------------------------------

def verify_acreage(declared_area_ha: Optional[float], measured_area_ha: Optional[float],
                    tolerance_pct: float = 10.0) -> Dict[str, Any]:
    if declared_area_ha is None or measured_area_ha is None:
        return {"available": False, "reason": "Both declared and satellite-measured area are needed."}

    discrepancy_pct = round(abs(declared_area_ha - measured_area_ha) / declared_area_ha * 100, 1) if declared_area_ha > 0 else None
    match = discrepancy_pct is not None and discrepancy_pct <= tolerance_pct
    over_declared = declared_area_ha > measured_area_ha

    return {
        "available": True,
        "declared_area_ha": declared_area_ha,
        "measured_area_ha": measured_area_ha,
        "discrepancy_pct": discrepancy_pct,
        "match": match,
        "flag": None if match else ("Over-declared" if over_declared else "Under-declared"),
        "tolerance_pct": tolerance_pct,
    }


# ---------------------------------------------------------------------------
# Crop Verification
# ---------------------------------------------------------------------------

def verify_crop(declared_crop: Optional[str], identification: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not declared_crop or not identification or not identification.get("identified_crop"):
        return {"available": False, "reason": "Both declared crop and a satellite crop identification are needed."}

    identified = identification["identified_crop"]
    match = declared_crop.strip().lower() == identified.strip().lower()

    return {
        "available": True,
        "declared_crop": declared_crop,
        "identified_crop": identified,
        "identification_confidence": identification.get("confidence"),
        "match": match,
        "flag": None if match else "Crop mismatch — declared crop does not match satellite-identified crop",
        "note": "Identification is a heuristic (see Crop Intelligence), not ML-verified — treat a mismatch as a reason to investigate, not proof of a false claim.",
    }


# ---------------------------------------------------------------------------
# Loss Estimation — from a before/after NDVI comparison (already fetched
# by historical_timeline_service.fetch_before_after_comparison)
# ---------------------------------------------------------------------------

def estimate_loss(before_after: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not before_after or not before_after.get("before") or not before_after.get("after"):
        return {"available": False, "reason": "Both before and after satellite scenes are needed (see /before-after)."}

    before_ndvi = before_after["before"].get("ndvi")
    after_ndvi = before_after["after"].get("ndvi")
    if before_ndvi is None or after_ndvi is None:
        return {"available": False, "reason": "NDVI values missing from one or both scenes."}

    if before_ndvi <= 0:
        return {"available": False, "reason": "Pre-event NDVI too low to compute a meaningful loss percentage."}

    drop = before_ndvi - after_ndvi
    loss_pct = round(max(0, min(100, (drop / before_ndvi) * 100)), 1)

    if loss_pct < 10:
        severity = "Negligible"
    elif loss_pct < 33:
        severity = "Minor"
    elif loss_pct < 67:
        severity = "Moderate"
    else:
        severity = "Severe"

    return {
        "available": True,
        "before_ndvi": before_ndvi,
        "after_ndvi": after_ndvi,
        "estimated_loss_pct": loss_pct,
        "severity": severity,
        "before_scene_date": before_after["before"].get("actual_scene_date"),
        "after_scene_date": before_after["after"].get("actual_scene_date"),
        "method": "NDVI drop between pre- and post-event scenes, as a % of the pre-event value — a vegetation-health proxy, not a direct yield-loss measurement.",
    }


# ---------------------------------------------------------------------------
# Fraud Detection — combines acreage/crop checks + a "ghost farm" check
# (is there any evidence of cropland here at all?)
# ---------------------------------------------------------------------------

def detect_fraud_signals(acreage_check: Optional[Dict[str, Any]], crop_check: Optional[Dict[str, Any]],
                          peak_ndvi: Optional[float]) -> Dict[str, Any]:
    flags: List[str] = []
    score = 0

    if acreage_check and acreage_check.get("available") and not acreage_check.get("match"):
        score += 35
        flags.append(f"Acreage {acreage_check['flag']} ({acreage_check['discrepancy_pct']}% discrepancy)")

    if crop_check and crop_check.get("available") and not crop_check.get("match"):
        score += 30
        flags.append(crop_check["flag"])

    # Ghost farm check — if satellite NEVER shows meaningful vegetation
    # (peak NDVI across the season stayed very low), there's likely no
    # active crop here at all, regardless of what was declared.
    if peak_ndvi is not None and peak_ndvi < 0.25:
        score += 35
        flags.append(f"No significant vegetation detected all season (peak NDVI {peak_ndvi}) — possible ghost farm")

    if score >= 60:
        risk = "High"
    elif score >= 30:
        risk = "Moderate"
    else:
        risk = "Low"

    return {
        "fraud_risk_score": score,
        "fraud_risk_level": risk,
        "flags": flags if flags else ["No discrepancies detected"],
        "note": "These are discrepancy flags for human review, not a fraud determination. Every High/Moderate flag needs field verification before any claim action.",
    }


# ---------------------------------------------------------------------------
# Claim Verification — the final recommendation combining everything above
# ---------------------------------------------------------------------------

def assess_claim(acreage_check: Dict[str, Any], crop_check: Dict[str, Any],
                  loss_estimate: Dict[str, Any], fraud: Dict[str, Any]) -> Dict[str, Any]:
    if fraud["fraud_risk_level"] == "High":
        recommendation = "INVESTIGATE"
        reason = "High fraud-risk signals present — route to field verification before any payout decision."
    elif not loss_estimate.get("available"):
        recommendation = "NEEDS_DATA"
        reason = "Cannot assess claim without a valid loss estimate — provide before/after imagery dates."
    elif loss_estimate.get("severity") in ("Moderate", "Severe") and fraud["fraud_risk_level"] == "Low":
        recommendation = "APPROVE_FOR_REVIEW"
        reason = f"Satellite evidence supports a {loss_estimate['severity'].lower()} loss ({loss_estimate['estimated_loss_pct']}%) with no fraud signals — recommend standard claims review, not auto-payout."
    elif loss_estimate.get("severity") in ("Negligible", "Minor"):
        recommendation = "LOW_PRIORITY"
        reason = f"Satellite evidence shows only {loss_estimate.get('severity','').lower()} vegetation impact ({loss_estimate.get('estimated_loss_pct')}%) — verify claimed loss cause doesn't fall outside what NDVI captures (e.g. quality/grade loss, not just yield)."
    else:
        recommendation = "INVESTIGATE"
        reason = "Moderate fraud-risk signals alongside the loss estimate — route to field verification."

    return {
        "recommendation": recommendation,
        "reason": reason,
        "acreage_check": acreage_check,
        "crop_check": crop_check,
        "loss_estimate": loss_estimate,
        "fraud_signals": fraud,
        "disclaimer": "This is a satellite-evidence triage tool, not an automatic claims decision. All recommendations require human review before any approval, rejection, or payout.",
    }
