"""
governance_service.py
=======================
DPDP-compliance-adjacent governance features from the Bhumi doc's
"Governance & Compliance" section:
  - Consent management (per data-use category, revocable)
  - Audit trail (immutable log of every BCIS score / auto-freeze /
    loan-stage-change / claim assessment)
  - Loan lifecycle tracking (5-stage, matching the Bhumi doc exactly)

This is NOT a full legal-compliance implementation — it's the data
structure and API a compliance officer would need to build the real
process on top of (retention policy, deletion-request handling, RBI
inspection export, etc. are still manual/organizational steps).

Audit logging is append-only and fails soft: log_event() never raises
into the caller — a missed audit log entry is bad, but a broken loan/
claim decision because logging failed is worse.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models import AuditLog, Loan, LOAN_STAGES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit Trail
# ---------------------------------------------------------------------------

def log_event(db: Session, event_type: str, summary: Optional[str] = None, detail: Optional[Dict[str, Any]] = None,
              farmer_id: Optional[str] = None, farm_id: Optional[str] = None,
              loan_id: Optional[str] = None, user_id: Optional[str] = None,
              lat: Optional[float] = None, lng: Optional[float] = None) -> None:
    """Append-only. Never raises — wraps its own DB errors so a logging
    failure can never break the credit/insurance decision it's logging.
    """
    try:
        entry = AuditLog(
            event_type=event_type, summary=summary, farmer_id=farmer_id, farm_id=farm_id, loan_id=loan_id,
            user_id=user_id, lat=lat, lng=lng,
            detail_json=json.dumps(detail, default=str) if detail is not None else None,
        )
        db.add(entry)
        db.commit()
    except Exception:
        logger.exception("Audit log write failed (non-fatal) for event_type=%s", event_type)
        try:
            db.rollback()
        except Exception:
            pass


def list_events(db: Session, event_type: Optional[str] = None, farmer_id: Optional[str] = None,
                 loan_id: Optional[str] = None, limit: int = 100) -> List[AuditLog]:
    q = db.query(AuditLog)
    if farmer_id:
        q = q.filter(AuditLog.farmer_id == farmer_id)
    if loan_id:
        q = q.filter(AuditLog.loan_id == loan_id)
    if event_type:
        q = q.filter(AuditLog.event_type == event_type)
    return q.order_by(AuditLog.created_at.desc()).limit(limit).all()


# ---------------------------------------------------------------------------
# Loan Lifecycle — 5 stages from the Bhumi doc
# ---------------------------------------------------------------------------

def create_loan(db: Session, farmer_id: str, farm_id: Optional[str] = None,
                 requested_amount_rs: Optional[float] = None, crop: Optional[str] = None,
                 season: Optional[str] = None) -> Loan:
    loan = Loan(
        farmer_id=farmer_id, farm_id=farm_id, stage="Application",
        requested_amount_rs=requested_amount_rs, crop=crop, season=season,
    )
    db.add(loan)
    db.commit()
    db.refresh(loan)
    return loan


def advance_loan_stage(db: Session, loan_id: str, new_stage: str,
                        approved_ceiling_rs: Optional[float] = None,
                        bcis_tier_at_approval: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if new_stage not in LOAN_STAGES:
        return {"error": f"Invalid stage — must be one of {LOAN_STAGES}"}

    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        return None

    current_idx = LOAN_STAGES.index(loan.stage)
    new_idx = LOAN_STAGES.index(new_stage)
    if new_idx < current_idx:
        return {"error": f"Cannot move backward from '{loan.stage}' to '{new_stage}'"}

    old_stage = loan.stage
    loan.stage = new_stage
    if approved_ceiling_rs is not None:
        loan.approved_ceiling_rs = approved_ceiling_rs
    if bcis_tier_at_approval is not None:
        loan.bcis_tier_at_approval = bcis_tier_at_approval
    db.commit()
    db.refresh(loan)

    return {"loan": loan.to_dict(), "old_stage": old_stage, "new_stage": new_stage}


def list_loans_for_farmer(db: Session, farmer_id: str) -> List[Loan]:
    return db.query(Loan).filter(Loan.farmer_id == farmer_id).order_by(Loan.created_at.desc()).all()


def get_loan(db: Session, loan_id: str) -> Optional[Loan]:
    return db.query(Loan).filter(Loan.id == loan_id).first()
