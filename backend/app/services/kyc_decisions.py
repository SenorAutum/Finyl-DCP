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


# --------------------------------------------------------------------------- #
# WP6 (F17-F20): evidence rules
# --------------------------------------------------------------------------- #
# A CRB report older than this is not "current" for a credit decision.
CRB_MAX_AGE_DAYS = 90
# Documents that can carry the client's ID number for the OCR check.
ID_DOC_TYPES = ("national_id_front", "national_id_back", "passport")
# custom_rules keys this engine knows how to enforce. Anything else is rejected
# explicitly rather than silently ignored (F18).
SUPPORTED_CUSTOM_RULES = {
    "min_age": "Minimum age in years (never below 18)",
    "max_age": "Maximum age in years",
    "require_documents": "List of document types that must be on file",
}

# Per-DCP strictness settings and their defaults (the strict choice). Each is a
# column on TenantValidationPrefs, edited in Validation Preferences.
MPESA_CONFIRMATION_MODES = ("second_person", "single_person")


def settings(db: Session, tenant_id: int):
    """The DCP's KYC process settings, with defaults when none are saved."""
    from types import SimpleNamespace
    p = _prefs(db, tenant_id)

    def g(name, default):
        v = getattr(p, name, None) if p is not None else None
        return default if v is None else v
    return SimpleNamespace(
        mpesa_confirmation_mode=g("mpesa_confirmation_mode", "second_person"),
        identity_edit_approvals=int(g("identity_edit_approvals", 2)),
        allow_review_override=bool(g("allow_review_override", True)),
        crb_max_age_days=int(g("crb_max_age_days", CRB_MAX_AGE_DAYS)),
        min_credit_score=g("min_credit_score", None),
    )


def validate_custom_rules(rules) -> list[str]:
    """Return a list of problems with a custom_rules payload (empty = valid)."""
    if rules in (None, {}):
        return []
    if not isinstance(rules, dict):
        return ["custom_rules must be an object"]
    problems = []
    for key, val in rules.items():
        if key not in SUPPORTED_CUSTOM_RULES:
            problems.append(f"unsupported rule '{key}' (supported: "
                            f"{', '.join(sorted(SUPPORTED_CUSTOM_RULES))})")
        elif key == "require_documents":
            from app.models import DOC_TYPES
            if not isinstance(val, list) or any(v not in DOC_TYPES for v in val):
                problems.append(f"require_documents must be a list drawn from {DOC_TYPES}")
        elif not isinstance(val, int) or isinstance(val, bool) or val < 0:
            problems.append(f"{key} must be a whole number")
    return problems


def _digits(v) -> str:
    return "".join(c for c in str(v or "") if c.isdigit())


def _msisdn(v) -> str:
    from app.services.sms import normalise_phone
    return normalise_phone(v or "")


def mpesa_evidence_current(borrower) -> bool:
    """The M-Pesa name was confirmed by a real lookup or by a second person, for
    the number the client has NOW. A change of number invalidates it."""
    return bool(getattr(borrower, "mpesa_validated", False)
                and getattr(borrower, "mpesa_validation_method", None) in ("provider", "manual")
                and _msisdn(getattr(borrower, "mpesa_validation_phone", None))
                == _msisdn(borrower.phone))


def evaluate(db: Session, *, tenant_id: int, client_id: int) -> KycDecision:
    """Run every check the DCP has made mandatory and return pass / fail /
    escalate. ``fail`` blocks approval and lending; ``escalate`` needs a person
    with ``kyc.resolve`` to approve with a written note."""
    from datetime import datetime, timedelta
    from app.models import ClientDocument, CrbCheck, Guarantor, KycMismatchEscalation

    prefs = _prefs(db, tenant_id)
    borrower = (db.query(Borrower)
                .filter(Borrower.id == client_id, Borrower.tenant_id == tenant_id).first())
    if not borrower:
        return KycDecision(DECISION_FAIL, ["client_not_found"])

    checks: dict = {}
    fails: list[str] = []
    reviews: list[str] = []

    # Defaults mirror the migration defaults when no prefs row exists.
    age_req = prefs.age_check_mandatory if prefs else True
    mpesa_req = prefs.mpesa_validation_mandatory if prefs else True
    face_req = prefs.face_validation_mandatory if prefs else False
    alt_req = prefs.alt_phone_validation_mandatory if prefs else False
    crb_req = prefs.crb_check_mandatory if prefs else False
    guar_req = prefs.guarantor_validation_mandatory if prefs else False
    ocr_req = prefs.ocr_mandatory if prefs else True
    ekyc_req = bool(getattr(prefs, "ekyc_mandatory", False)) if prefs else False
    custom = (prefs.custom_rules or {}) if prefs else {}
    if not isinstance(custom, dict):
        custom = {"<invalid>": custom}

    # --- custom rules the engine cannot enforce: say so, never ignore (F18) --
    for key in custom:
        if key not in SUPPORTED_CUSTOM_RULES:
            fails.append(f"custom_rule_unsupported:{key}")

    # --- Age ------------------------------------------------------------------
    age = _age_years(getattr(borrower, "date_of_birth", None))
    min_age = custom.get("min_age") if isinstance(custom.get("min_age"), int) else 18
    min_age = max(18, min_age)        # never below the legal minimum
    max_age = custom.get("max_age") if isinstance(custom.get("max_age"), int) else None
    if age_req or "min_age" in custom or max_age is not None:
        ok = age is not None and age >= min_age and (max_age is None or age <= max_age)
        checks["age"] = {"pass": ok, "age": age, "min": min_age, "max": max_age}
        if not ok:
            fails.append("age_below_18_or_unknown" if (age is None or age < 18)
                         else "age_outside_allowed_range")

    # --- M-Pesa name confirmation (F15) ---------------------------------------
    if mpesa_req:
        ok = mpesa_evidence_current(borrower)
        checks["mpesa"] = {"pass": ok,
                           "method": getattr(borrower, "mpesa_validation_method", None)}
        if not ok:
            fails.append("mpesa_not_validated")

    # --- eKYC: a failed registry check is evidence against the client, whether
    #     or not the DCP made the check mandatory (F19) ------------------------
    ekyc = getattr(borrower, "ekyc_status", None)
    if ekyc or ekyc_req:
        checks["ekyc"] = {"pass": ekyc == "verified", "status": ekyc or "not_run",
                          "mandatory": ekyc_req}
        if ekyc == "not_verified":
            fails.append("ekyc_not_verified")
        elif ekyc_req and ekyc != "verified":
            fails.append("ekyc_error" if ekyc == "error" else "ekyc_not_run")

    # --- Face validation --------------------------------------------------------
    if face_req:
        fv = (db.query(FaceValidationLog)
              .filter(FaceValidationLog.tenant_id == tenant_id,
                      FaceValidationLog.client_id == client_id)
              .order_by(FaceValidationLog.validated_at.desc()).first())
        res = fv.result if fv else "not_run"
        checks["face"] = {"pass": res == "pass", "result": res}
        if res == "manual_review":
            reviews.append("face_manual_review")
        elif res != "pass":
            fails.append(f"face_{res}")

    # --- Alt phone ----------------------------------------------------------------
    if alt_req:
        ok = bool(getattr(borrower, "alt_phone_validated", False))
        checks["alt_phone"] = {"pass": ok}
        if not ok:
            fails.append("alt_phone_not_validated")

    # --- CRB: a successful, current report for THIS client (F17) ---------------
    st = settings(db, tenant_id)
    min_score = st.min_credit_score
    if crb_req or min_score is not None:
        max_days = st.crb_max_age_days if st.crb_max_age_days > 0 else CRB_MAX_AGE_DAYS
        latest_ok = (db.query(CrbCheck)
                     .filter(CrbCheck.tenant_id == tenant_id, CrbCheck.client_id == client_id,
                             CrbCheck.status == "ok")
                     .order_by(CrbCheck.created_at.desc(), CrbCheck.id.desc()).first())
        any_run = latest_ok or (db.query(CrbCheck.id)
                                .filter(CrbCheck.tenant_id == tenant_id,
                                        CrbCheck.client_id == client_id).first())
        current = bool(latest_ok and latest_ok.created_at
                       and latest_ok.created_at >= datetime.utcnow() - timedelta(days=max_days))
        score = latest_ok.credit_score if latest_ok else None
        score_ok = (min_score is None or (score is not None and score >= min_score))
        checks["crb"] = {"pass": current and score_ok, "score": score,
                         "checked_at": latest_ok.created_at.isoformat()
                         if latest_ok and latest_ok.created_at else None,
                         "max_age_days": max_days}
        if not any_run:
            fails.append("crb_not_run")
        elif not latest_ok:
            fails.append("crb_failed")
        elif not current:
            fails.append("crb_out_of_date")
        elif not score_ok:
            fails.append("crb_score_below_minimum")

    # --- ID document read by OCR, and its number matches the profile (F18) -----
    if ocr_req:
        docs = (db.query(ClientDocument)
                .filter(ClientDocument.tenant_id == tenant_id,
                        ClientDocument.client_id == client_id,
                        ClientDocument.doc_type.in_(ID_DOC_TYPES),
                        ClientDocument.ocr_applied.is_(True)).all())
        want = _digits(borrower.national_id)
        read = [_digits((d.ocr_field_mapping or {}).get("national_id")) for d in docs]
        read = [r for r in read if r]
        ok = bool(want) and want in read
        checks["ocr"] = {"pass": ok, "documents_read": len(docs)}
        if ok:
            pass
        elif read:
            fails.append("ocr_id_number_mismatch")
        elif docs:
            reviews.append("ocr_id_number_unreadable")
        else:
            fails.append("ocr_not_run")

    # --- Required documents (custom rule) -------------------------------------
    req_docs = custom.get("require_documents")
    if isinstance(req_docs, list) and req_docs:
        have = {t for (t,) in db.query(ClientDocument.doc_type)
                .filter(ClientDocument.tenant_id == tenant_id,
                        ClientDocument.client_id == client_id).all()}
        missing = [t for t in req_docs if t not in have]
        checks["documents"] = {"pass": not missing, "missing": missing}
        for t in missing:
            fails.append(f"document_missing:{t}")

    # --- Guarantor -------------------------------------------------------------
    if guar_req:
        g = (db.query(Guarantor)
             .filter(Guarantor.tenant_id == tenant_id, Guarantor.client_id == client_id,
                     Guarantor.kyc_status == "validated").first())
        checks["guarantor"] = {"pass": g is not None}
        if g is None:
            fails.append("guarantor_not_validated")

    # --- An open KYC mismatch escalation always needs a person ------------------
    open_esc = (db.query(KycMismatchEscalation.id)
                .filter(KycMismatchEscalation.tenant_id == tenant_id,
                        KycMismatchEscalation.client_id == client_id,
                        KycMismatchEscalation.status == "open")
                .count())
    if open_esc:
        checks["escalations"] = {"pass": False, "open": open_esc}
        reviews.append("kyc_escalation_open")

    if fails:
        return KycDecision(DECISION_FAIL, fails + reviews, checks)
    if reviews:
        return KycDecision(DECISION_ESCALATE, reviews, checks)
    return KycDecision(DECISION_PASS, [], checks)


# Plain-language explanations for the reasons above (shown to approvers).
_EXPLAIN = {
    "client_not_found": "Client not found.",
    "age_below_18_or_unknown": "Date of birth missing or the client is under 18.",
    "age_outside_allowed_range": "Age is outside the range this DCP allows.",
    "mpesa_not_validated": "M-Pesa name not confirmed for the client's current number. "
                           "Confirm it on the client page (Confirm M-Pesa name).",
    "ekyc_not_verified": "The ID registry (eKYC) check did NOT verify this person.",
    "ekyc_error": "The eKYC check failed to complete; run it again.",
    "ekyc_not_run": "eKYC is mandatory and has not been run.",
    "face_fail": "Face check failed.",
    "face_not_run": "Face check is mandatory and has not been run.",
    "face_manual_review": "Face check needs manual review.",
    "alt_phone_not_validated": "Alternate phone is mandatory and not verified.",
    "crb_not_run": "CRB check is mandatory and has not been run.",
    "crb_failed": "The CRB check did not return a result (error / not configured).",
    "crb_out_of_date": "The latest CRB report is too old; run a new check.",
    "crb_score_below_minimum": "CRB score is below this DCP's minimum.",
    "ocr_not_run": "No ID document has been read (OCR). Upload the ID and run 'Read ID'.",
    "ocr_id_number_mismatch": "The ID number read from the ID document does not match "
                              "the National ID on the profile.",
    "ocr_id_number_unreadable": "The ID document was read but its number could not be; "
                                "check the ID by eye.",
    "guarantor_not_validated": "A validated guarantor is required.",
    "kyc_escalation_open": "There is an open KYC mismatch escalation for this client.",
}


def explain(reasons: list[str]) -> list[str]:
    out = []
    for r in reasons:
        if r.startswith("custom_rule_unsupported:"):
            out.append(f"KYC setting '{r.split(':', 1)[1]}' is not a rule the system can "
                       "enforce; fix the DCP's custom rules.")
        elif r.startswith("document_missing:"):
            out.append(f"Required document missing: {r.split(':', 1)[1].replace('_', ' ')}.")
        else:
            out.append(_EXPLAIN.get(r, r.replace("_", " ")))
    return out
