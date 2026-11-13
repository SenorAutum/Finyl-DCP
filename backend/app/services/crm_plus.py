"""WP17 — CRM expansion.

  * A client's full history in one timeline: interactions logged by staff, calls,
    complaints, SMS, loans, payments, promises to pay and tasks.
  * Follow-up tasks assigned to staff (from an interaction's next step, or by hand).
  * Segments: saved filters over clients (branch, gender, sector, loan state,
    repaid loans, loan cycle, dormant for N days, joined recently, marketing consent).
  * SMS campaigns to a segment. Marketing campaigns go only to clients who agreed
    to marketing (Data Protection Act 2019, s.37) and always end with an opt-out
    line; every campaign skips opted-out numbers. A second person approves before
    anything is sent.
  * Pipeline report: leads by stage, by officer and by month, conversion.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import (Borrower, CallLog, Complaint, CrmCampaign, CrmCampaignRecipient, CrmInteraction, CrmLead,
                        CrmSegment, CrmTask, KycConsent, Loan, PromiseToPay, Repayment, SmsLog, Staff, User)
from app.services import ledger as L
from app.services import sms

OPEN = ("active", "overdue", "defaulted", "written_off")
LOAN_STATES = ("any", "open", "no_open", "in_arrears", "never_borrowed", "repaid_no_open")
RULE_KEYS = ("branch_ids", "gender", "sector", "loan_state", "min_repaid_loans", "min_cycle", "dormant_days",
             "joined_within_days", "marketing_consent")
MAX_RECIPIENTS = 5000


def clean_rules(rules: dict) -> dict:
    out = {}
    for k, v in (rules or {}).items():
        if k not in RULE_KEYS:
            raise HTTPException(422, f"Unknown segment rule '{k}'")
        if v in (None, "", []):
            continue
        if k == "branch_ids":
            out[k] = [int(x) for x in v]
        elif k == "gender":
            if v not in ("female", "male"):
                raise HTTPException(422, "gender must be female or male")
            out[k] = v
        elif k == "sector":
            out[k] = str(v)[:60]
        elif k == "loan_state":
            if v not in LOAN_STATES:
                raise HTTPException(422, f"loan_state must be one of {', '.join(LOAN_STATES)}")
            out[k] = v
        elif k == "marketing_consent":
            out[k] = bool(v)
        else:
            n = int(v)
            if n < 0:
                raise HTTPException(422, f"{k} cannot be negative")
            out[k] = n
    return out


def _consented(db, tenant_id, ids) -> set[int]:
    """Clients whose latest consent record agrees to marketing."""
    latest = {}
    for c in (db.query(KycConsent).filter(KycConsent.tenant_id == tenant_id, KycConsent.borrower_id.in_(ids or [-1]))
              .order_by(KycConsent.consented_at)):
        latest[c.borrower_id] = bool(c.consent_marketing)
    return {b for b, ok in latest.items() if ok}


def evaluate(db: Session, tenant_id: int, rules: dict, scope=None) -> list[Borrower]:
    rules = clean_rules(rules)
    q = db.query(Borrower).filter(Borrower.tenant_id == tenant_id, Borrower.is_active.isnot(False),
                                  or_(Borrower.profile_status == "approved", Borrower.profile_status.is_(None)))
    if scope is not None:
        q = scope.apply_client(q, Borrower)
    if rules.get("branch_ids"):
        q = q.filter(Borrower.branch_id.in_(rules["branch_ids"]))
    if rules.get("gender"):
        q = q.filter(func.lower(Borrower.gender) == rules["gender"])
    if rules.get("sector"):
        q = q.filter(Borrower.business_sector.ilike(f"%{rules['sector']}%"))
    if rules.get("joined_within_days") is not None:
        q = q.filter(Borrower.created_at >= datetime.utcnow() - timedelta(days=rules["joined_within_days"]))
    people = q.all()
    ids = [b.id for b in people]
    if not ids:
        return []
    today = L.today()
    agg = {}
    for bid, status, due, cycle in db.query(Loan.borrower_id, Loan.status, Loan.due_date, Loan.loan_cycle_number) \
            .filter(Loan.tenant_id == tenant_id, Loan.borrower_id.in_(ids)):
        a = agg.setdefault(bid, {"open": 0, "late": 0, "paid": 0, "any": 0, "cycle": 0})
        a["any"] += 1
        a["cycle"] = max(a["cycle"], cycle or 0)
        if status in OPEN:
            a["open"] += 1
            if due and due < today:
                a["late"] += 1
        if status == "paid":
            a["paid"] += 1
    last_paid = dict(db.query(Loan.borrower_id, func.max(Repayment.payment_date))
                     .join(Repayment, Repayment.loan_id == Loan.id)
                     .filter(Loan.tenant_id == tenant_id, Loan.borrower_id.in_(ids)).group_by(Loan.borrower_id).all())
    consent = _consented(db, tenant_id, ids) if rules.get("marketing_consent") else None
    out = []
    for b in people:
        a = agg.get(b.id, {"open": 0, "late": 0, "paid": 0, "any": 0, "cycle": 0})
        st = rules.get("loan_state", "any")
        if st == "open" and not a["open"]:
            continue
        if st == "no_open" and a["open"]:
            continue
        if st == "in_arrears" and not a["late"]:
            continue
        if st == "never_borrowed" and a["any"]:
            continue
        if st == "repaid_no_open" and (a["open"] or not a["paid"]):
            continue
        if a["paid"] < rules.get("min_repaid_loans", 0):
            continue
        if a["cycle"] < rules.get("min_cycle", 0):
            continue
        if rules.get("dormant_days") is not None:
            lp = last_paid.get(b.id)
            if a["open"] or lp is None or lp > datetime.utcnow() - timedelta(days=rules["dormant_days"]):
                continue
        if consent is not None and b.id not in consent:
            continue
        out.append(b)
    return out


# --------------------------------------------------------------------------- campaigns
def render(db, tenant_id, message: str, b: Borrower, purpose: str) -> str:
    ctx = {"first_name": b.first_name, "last_name": b.last_name, "dcp_name": sms.dcp_name(db, tenant_id)}
    try:
        ctx["paybill"] = sms._paybill(db, tenant_id)
    except Exception:  # noqa: BLE001
        ctx["paybill"] = ""
    text = sms.render_body(message, ctx).strip()
    if purpose == "marketing" and "STOP" not in text.upper():
        text = f"{text} Reply STOP to opt out."
    return text


def send_campaign(db, c: CrmCampaign) -> CrmCampaign:
    """Send now. Skips: no phone, opted out, marketing without consent, sent already."""
    if c.status not in ("approved",):
        raise HTTPException(409, f"This campaign is {c.status}")
    seg = db.get(CrmSegment, c.segment_id)
    people = evaluate(db, c.tenant_id, seg.rules if seg else {})[:MAX_RECIPIENTS]
    consent = _consented(db, c.tenant_id, [b.id for b in people]) if c.purpose == "marketing" else None
    done = {r for (r,) in db.query(CrmCampaignRecipient.borrower_id).filter(CrmCampaignRecipient.campaign_id == c.id)}
    c.status, c.started_at = "sending", datetime.utcnow()
    db.flush()
    for b in people:
        if b.id in done:
            continue
        phone = sms.normalise_phone(b.phone or "")
        reason = None
        if not phone:
            reason = "no phone number"
        elif sms.is_opted_out(db, c.tenant_id, phone):
            reason = "opted out"
        elif consent is not None and b.id not in consent:
            reason = "no marketing consent"
        if reason:
            db.add(CrmCampaignRecipient(campaign_id=c.id, borrower_id=b.id, phone=phone or None, status="skipped", reason=reason))
            c.skipped += 1
            continue
        log = sms.send_sms(db, c.tenant_id, phone, render(db, c.tenant_id, c.message, b, c.purpose), trigger_type="campaign")
        ok = log.status == "sent"
        db.add(CrmCampaignRecipient(campaign_id=c.id, borrower_id=b.id, phone=phone, status="sent" if ok else "failed",
                                    reason=None if ok else (log.error or "")[:60], sms_log_id=log.id))
        if ok:
            c.sent += 1
        else:
            c.failed += 1
    c.recipients = c.sent + c.failed + c.skipped
    c.status, c.finished_at = "sent", datetime.utcnow()
    db.flush()
    return c


def send_due(db) -> int:
    """Scheduler: send approved campaigns whose time has come."""
    n = 0
    for c in db.query(CrmCampaign).filter(CrmCampaign.status == "approved",
                                          CrmCampaign.send_at <= datetime.utcnow()).limit(20):
        try:
            send_campaign(db, c)
            db.commit()
            n += 1
        except Exception:  # noqa: BLE001
            db.rollback()
    return n


# --------------------------------------------------------------------------- timeline
def timeline(db, tenant_id, b: Borrower, limit: int = 200) -> list[dict]:
    ev = []
    users = {u.id: u.full_name for u in db.query(User).filter(User.tenant_id == tenant_id)}
    staff = {s.id: s.name for s in db.query(Staff).filter(Staff.tenant_id == tenant_id)}
    for i in db.query(CrmInteraction).filter(CrmInteraction.borrower_id == b.id):
        ev.append({"at": i.happened_at, "type": "interaction", "kind": i.kind, "title": i.subject or i.kind.title(),
                   "detail": i.notes, "outcome": i.outcome, "by": users.get(i.user_id), "direction": i.direction})
    for c in db.query(CallLog).filter(CallLog.borrower_id == b.id):
        ev.append({"at": c.call_date, "type": "call", "kind": "call", "title": f"Call: {c.call_outcome.replace('_', ' ')}",
                   "detail": c.notes, "by": staff.get(c.agent_id)})
    for c in db.query(Complaint).filter(Complaint.borrower_id == b.id):
        ev.append({"at": c.created_at, "type": "complaint", "kind": "complaint",
                   "title": f"Complaint {c.ticket_id}: {c.category.replace('_', ' ')} ({c.status})", "detail": c.description})
    phone = sms.normalise_phone(b.phone or "")
    if phone:
        for s in db.query(SmsLog).filter(SmsLog.tenant_id == tenant_id, SmsLog.recipient_phone == phone) \
                .order_by(SmsLog.sent_at.desc()).limit(50):
            ev.append({"at": s.sent_at, "type": "sms", "kind": "sms", "title": f"SMS ({s.trigger_type.replace('_', ' ')}, {(s.status or '').replace('_', ' ')})",
                       "detail": s.message if s.trigger_type != "otp" else "One-time code"})
    loans = db.query(Loan).filter(Loan.borrower_id == b.id, Loan.tenant_id == tenant_id).all()
    for ln in loans:
        if ln.application_date:
            ev.append({"at": datetime.combine(ln.application_date, datetime.min.time()), "type": "loan", "kind": "loan",
                       "title": f"Applied for {ln.account_number}: KES {float(ln.principal):,.0f}", "loan_id": ln.id, "date_only": True})
        if ln.disbursement_date:
            ev.append({"at": datetime.combine(ln.disbursement_date, datetime.min.time()) + timedelta(hours=1), "type": "loan",
                       "kind": "loan", "title": f"{ln.account_number} paid out", "loan_id": ln.id, "date_only": True})
    ids = [ln.id for ln in loans]
    for r in db.query(Repayment).filter(Repayment.loan_id.in_(ids or [-1])):
        ev.append({"at": r.payment_date, "type": "payment", "kind": "payment",
                   "title": f"Paid KES {float(r.amount):,.0f}", "detail": r.mpesa_ref, "loan_id": r.loan_id})
    for p in db.query(PromiseToPay).filter(PromiseToPay.client_id == b.id):
        ev.append({"at": datetime.combine(p.ptp_date, datetime.min.time()), "type": "promise", "kind": "promise",
                   "title": f"Promise to pay KES {float(p.amount or 0):,.0f} ({p.status})", "detail": p.notes, "loan_id": p.loan_id, "date_only": True})
    for t in db.query(CrmTask).filter(CrmTask.borrower_id == b.id):
        ev.append({"at": datetime.combine(t.due_date, datetime.min.time()), "type": "task", "kind": "task",
                   "title": f"Task: {t.title} ({t.status})", "detail": t.notes, "by": users.get(t.assigned_user_id), "date_only": True})
    ev = [e for e in ev if e["at"] is not None]
    ev.sort(key=lambda e: e["at"], reverse=True)
    for e in ev[:limit]:
        e["at"] = e["at"].isoformat()
    return ev[:limit]


# --------------------------------------------------------------------------- pipeline
def pipeline_report(db, tenant_id, months: int = 6) -> dict:
    from app.models import CRM_STAGES
    leads = db.query(CrmLead).filter(CrmLead.tenant_id == tenant_id).all()
    staff = {s.id: s.name for s in db.query(Staff).filter(Staff.tenant_id == tenant_id)}
    by_stage = {s: 0 for s in CRM_STAGES}
    by_officer = {}
    converted = 0
    days = []
    for ld in leads:
        by_stage[ld.stage] = by_stage.get(ld.stage, 0) + 1
        o = by_officer.setdefault(staff.get(ld.assigned_staff_id, "Unassigned"), {"leads": 0, "converted": 0, "value": 0.0})
        o["leads"] += 1
        o["value"] += float(ld.estimated_loan_amount or 0)
        if ld.converted_to_client_id or ld.stage == "disbursed":
            converted += 1
            o["converted"] += 1
            if ld.conversion_triggered_at and ld.created_at:
                days.append((ld.conversion_triggered_at.replace(tzinfo=None) - ld.created_at).days)
    start = (L.today().replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)
    monthly = {}
    for ld in leads:
        if ld.created_at and ld.created_at.date() >= start:
            k = ld.created_at.strftime("%Y-%m")
            m = monthly.setdefault(k, {"month": k, "new": 0, "converted": 0})
            m["new"] += 1
            if ld.converted_to_client_id or ld.stage == "disbursed":
                m["converted"] += 1
    total = len(leads)
    return {"total": total, "converted": converted, "conversion_pct": round(converted / total * 100, 1) if total else 0,
            "avg_days_to_convert": round(sum(days) / len(days), 1) if days else None,
            "by_stage": [{"stage": s, "count": by_stage.get(s, 0)} for s in CRM_STAGES],
            "by_officer": sorted([{"officer": k, **v, "conversion_pct": round(v["converted"] / v["leads"] * 100, 1) if v["leads"] else 0}
                                  for k, v in by_officer.items()], key=lambda r: -r["leads"]),
            "monthly": [monthly[k] for k in sorted(monthly)],
            "pipeline_value": round(sum(float(ld.estimated_loan_amount or 0) for ld in leads
                                          if not ld.converted_to_client_id and ld.stage != "disbursed"), 2)}
