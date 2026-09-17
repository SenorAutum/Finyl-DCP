"""Centralised KYC decision engine (Phase 2).

Runs all configured checks (age, ID/OCR, M-Pesa, face, CRB, alt-phone, guarantor)
in sequence per the tenant's TenantValidationPrefs and emits a single decision:
pass | fail | escalate, with a reason list. This is the authoritative layer —
routers must not hand-wave a pass around it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import (Borrower, TenantValidationPrefs, FaceValidationLog)

DECISION_PASS = "pass"
DECISION_FAIL = "fail"
DECISION_ESCALATE = "escalate"


@dataclass
class KycDecision:
    decision: str
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"decision": self.decision, "reasons": self.reasons, "checks": self.checks}


def _prefs(db: Session, tenant_id: int) -> TenantValidationPrefs | None:
    return (db.query(TenantValidationPrefs)
            .filter(TenantValidationPrefs.tenant_id == tenant_id).first())


def _age_years(dob) -> int | None:
    if not dob:
        return None
    if isinstance(dob, str):
        try:
            from datetime import datetime
            dob = datetime.fromisoformat(dob).date()
        except Exception:
            return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def evaluate(db: Session, *, tenant_id: int, client_id: int) -> KycDecision:
    prefs = _prefs(db, tenant_id)
    borrower = (db.query(Borrower)
                .filter(Borrower.id == client_id, Borrower.tenant_id == tenant_id).first())
    if not borrower:
        return KycDecision(DECISION_FAIL, ["client_not_found"])

    checks: dict = {}
    reasons: list[str] = []
    escalate = False

    # Defaults mirror the migration defaults when no prefs row exists.
    age_req = prefs.age_check_mandatory if prefs else True
    mpesa_req = prefs.mpesa_validation_mandatory if prefs else True
    face_req = prefs.face_validation_mandatory if prefs else False
    alt_req = prefs.alt_phone_validation_mandatory if prefs else False
    crb_req = prefs.crb_check_mandatory if prefs else False
    guar_req = prefs.guarantor_validation_mandatory if prefs else False

    # --- Age (>= 18) -------------------------------------------------------
    if age_req:
        age = _age_years(getattr(borrower, "date_of_birth", None))
        ok = age is not None and age >= 18
        checks["age"] = {"pass": ok, "age": age}
        if not ok:
            reasons.append("age_below_18_or_unknown")

    # --- M-Pesa name validation -------------------------------------------
    if mpesa_req:
        ok = bool(getattr(borrower, "mpesa_validated", False) or
                  getattr(borrower, "wallet_validated", False))
        # ClientMobileWallet may carry validation; fall back to borrower flag.
        checks["mpesa"] = {"pass": ok}
        if not ok:
            reasons.append("mpesa_not_validated")

    # --- Face validation ---------------------------------------------------
    if face_req:
        fv = (db.query(FaceValidationLog)
              .filter(FaceValidationLog.tenant_id == tenant_id,
                      FaceValidationLog.client_id == client_id)
              .order_by(FaceValidationLog.validated_at.desc()).first())
        res = fv.result if fv else "not_run"
        checks["face"] = {"pass": res == "pass", "result": res}
        if res == "fail" or res == "not_run":
            reasons.append(f"face_{res}")
        elif res == "manual_review":
            escalate = True
            reasons.append("face_manual_review")

    # --- Alt phone ---------------------------------------------------------
    if alt_req:
        ok = bool(getattr(borrower, "alt_phone_validated", False))
        checks["alt_phone"] = {"pass": ok}
        if not ok:
            reasons.append("alt_phone_not_validated")

    # --- CRB ---------------------------------------------------------------
    if crb_req:
        from app.models import CrbCheck
        crb = (db.query(CrbCheck)
               .filter(CrbCheck.tenant_id == tenant_id, CrbCheck.borrower_id == client_id)
               .order_by(CrbCheck.id.desc()).first())
        checks["crb"] = {"pass": crb is not None}
        if crb is None:
            reasons.append("crb_not_run")

    # --- Guarantor ---------------------------------------------------------
    if guar_req:
        from app.models import Guarantor
        g = (db.query(Guarantor)
             .filter(Guarantor.tenant_id == tenant_id, Guarantor.client_id == client_id,
                     Guarantor.kyc_status == "validated").first())
        checks["guarantor"] = {"pass": g is not None}
        if g is None:
            reasons.append("guarantor_not_validated")

    hard_fail = [r for r in reasons if not r.endswith("manual_review")]
    if hard_fail:
        return KycDecision(DECISION_FAIL, reasons, checks)
    if escalate:
        return KycDecision(DECISION_ESCALATE, reasons, checks)
    return KycDecision(DECISION_PASS, reasons, checks)
