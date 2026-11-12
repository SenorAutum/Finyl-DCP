"""WP13 — the Finyl command centre API (/api/v1/platform).

For the Finyl super admin and Finyl-side users (staff and partners). Every
endpoint checks the person's switch for that part, and every DCP figure is
limited to the DCPs assigned to them. Opening a DCP's dashboard is recorded in
that DCP's audit trail, so a DCP can be told who at Finyl looked at its data.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, write_audit
from app.core.security import hash_password
from app.models import (PlatformAlert, PlatformAlertRecipient, PlatformFacility, PlatformInvoice,
                        PlatformLead, PlatformLeadEvent, PlatformUnmatchedPayment,
                        PlatformUserAccess, Tenant, User)
from app.services import platform_alerts as alerts_svc
from app.services import platform_billing as billing
from app.services import platform_lending as lending
from app.services import platform_metrics as metrics
from app.services.platform_access import (PLATFORM_PERMISSIONS, PLATFORM_ROLE, PRESETS,
                                          PlatformAccess, access_for, check_tenant,
                                          require_platform, visible_tenants)

router = APIRouter(prefix="/api/v1/platform", tags=["finyl command centre"])

STAGES = ["lead", "contacted", "demo", "proposal", "signed", "live", "lost"]
STAGE_LABELS = {"lead": "Lead", "contacted": "Contacted", "demo": "Demo done", "proposal": "Proposal sent",
                "signed": "Signed", "live": "Live on Finyl", "lost": "Lost"}


def _ids(acc: PlatformAccess, db) -> set[int] | None:
    return None if acc.all_tenants else set(acc.tenant_ids)


# --------------------------------------------------------------------------- #
# Who am I
# --------------------------------------------------------------------------- #
@router.get("/me")
def platform_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    acc = access_for(db, user)
    return {**acc.as_dict(), "catalog": PLATFORM_PERMISSIONS}


# --------------------------------------------------------------------------- #
# Command centre
# --------------------------------------------------------------------------- #
@router.get("/overview")
def overview(days: int = 30, acc: PlatformAccess = Depends(require_platform("platform.dashboard")),
             db: Session = Depends(get_db)):
    days = max(1, min(int(days), 365))
    tenants = visible_tenants(acc, db)
    counts = alerts_svc.recent_counts(db, {t.id for t in tenants})
    rows = [metrics.summary(db, t, days, counts.get(t.id)) for t in tenants]
    for r in rows:
        r["alerts"] = counts.get(r["id"], {"critical": 0, "warning": 0, "info": 0})
    out = {"days": days, "tenants": rows,
           "totals": {"dcps": len(rows), "live": sum(1 for r in rows if r["active"]),
                      "outstanding": round(sum(r["book"]["outstanding"] for r in rows), 2),
                      "par30": round(sum(r["book"]["par30"] for r in rows), 2),
                      "disbursed": round(sum(r["window"]["disbursed"] for r in rows), 2),
                      "collected": round(sum(r["window"]["collected"] for r in rows), 2),
                      "clients": sum(r["operations"]["clients"] for r in rows),
                      "sms_sent": sum(r["sms"]["sent"] for r in rows),
                      "sms_revenue": round(sum(r["sms"]["revenue"] for r in rows), 2),
                      "sms_margin": round(sum(r["sms"]["margin"] for r in rows), 2),
                      "red": sum(1 for r in rows if r["health"]["band"] == "red"),
                      "open_alerts": sum(sum(c.values()) for c in counts.values())}}
    if acc.can("platform.revenue"):
        ids = _ids(acc, db)
        q = db.query(PlatformInvoice).filter(PlatformInvoice.status.in_(("issued", "part_paid")))
        if ids is not None:
            q = q.filter(PlatformInvoice.tenant_id.in_(sorted(ids) or [-1]))
        out["totals"]["unpaid_invoices"] = round(sum(float(i.total) - float(i.paid) for i in q.all()), 2)
        rev = billing.revenue(db, ids, months=2)
        out["totals"]["last_invoiced"] = ({"label": rev[-1]["label"], "total": rev[-1]["total"]}
                                          if rev else None)
    if acc.can("platform.lending"):
        facs = acc.tenant_filter(db.query(PlatformFacility), PlatformFacility.tenant_id).filter(
            PlatformFacility.status != "closed").all()
        pos = [lending.position(db, f) for f in facs]
        out["totals"]["lent_outstanding"] = round(sum(p["principal_outstanding"] for p in pos), 2)
        out["totals"]["lent_arrears"] = round(sum(p["arrears"] for p in pos), 2)
    if acc.can("platform.pipeline"):
        out["totals"]["pipeline_open"] = (db.query(PlatformLead)
                                          .filter(~PlatformLead.stage.in_(("live", "lost"))).count())
    return out


@router.get("/tenants/{tenant_id}")
def tenant_dashboard(tenant_id: int, request: Request, days: int = 30,
                     acc: PlatformAccess = Depends(require_platform("platform.dashboard")),
                     db: Session = Depends(get_db)):
    """Everything about one DCP on one page."""
    t = check_tenant(acc, db, tenant_id)
    days = max(1, min(int(days), 365))
    counts = alerts_svc.recent_counts(db, {t.id})
    out = metrics.summary(db, t, days, counts.get(t.id))
    since = datetime.utcnow() - timedelta(days=days)
    out.update({
        "days": days,
        "monthly": metrics.monthly(db, t.id, 12),
        "services": metrics.services_usage(db, t.id, since),
        "trend": metrics.trend(db, t.id, 90),
        "alerts": [{"id": a.id, "kind": a.kind, "severity": a.severity, "title": a.title,
                    "count": a.count, "last_seen": a.last_seen.isoformat() + "Z"}
                   for a in db.query(PlatformAlert).filter(PlatformAlert.tenant_id == t.id,
                                                            PlatformAlert.status != "resolved")
                   .order_by(PlatformAlert.last_seen.desc()).limit(20)],
        "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
    })
    if acc.can("platform.revenue"):
        out["billing"] = billing.billing_dict(db, t.id)
        out["invoices"] = [billing.invoice_dict(db, i) for i in db.query(PlatformInvoice)
                           .filter(PlatformInvoice.tenant_id == t.id)
                           .order_by(PlatformInvoice.period_start.desc()).limit(12)]
    if acc.can("platform.lending"):
        out["facilities"] = [lending.facility_dict(db, f) for f in db.query(PlatformFacility)
                             .filter(PlatformFacility.tenant_id == t.id).order_by(PlatformFacility.id)]
    lead = db.query(PlatformLead).filter(PlatformLead.tenant_id == t.id).first()
    if lead is not None and acc.can("platform.pipeline"):
        out["lead"] = {"id": lead.id, "stage": lead.stage, "contact_name": lead.contact_name,
                       "contact_phone": lead.contact_phone, "contact_email": lead.contact_email}
    write_audit(db, tenant_id=t.id, user=acc.user, action="platform.dcp_viewed", entity_type="tenant",
                entity_id=t.id, details={"by": acc.user.email}, request=request)
    db.commit()
    return out


# --------------------------------------------------------------------------- #
# Billing
# --------------------------------------------------------------------------- #
class PricingIn(BaseModel):
    onboarding_fee: float | None = None
    subscription_rate_pct: float | None = None
    subscription_minimum: float | None = None
    branch_fee: float | None = None
    branches_included: int | None = None
    bill_sms: bool | None = None
    payment_terms_days: int | None = None
    invoice_day: int | None = None


@router.get("/pricing")
def get_pricing(acc: PlatformAccess = Depends(require_platform("platform.revenue")),
                db: Session = Depends(get_db)):
    p = billing.pricing(db)
    db.commit()
    return billing.pricing_dict(p)


@router.put("/pricing")
def put_pricing(body: PricingIn, request: Request,
                acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
                db: Session = Depends(get_db)):
    p = billing.pricing(db)
    data = body.model_dump(exclude_none=True)
    for k in ("onboarding_fee", "subscription_minimum", "branch_fee"):
        if k in data and data[k] < 0:
            raise HTTPException(422, f"{k} cannot be negative")
    if "subscription_rate_pct" in data and not (0 <= data["subscription_rate_pct"] <= 20):
        raise HTTPException(422, "The subscription rate must be between 0% and 20%")
    if "invoice_day" in data and not (1 <= data["invoice_day"] <= 28):
        raise HTTPException(422, "The invoice day must be 1-28")
    if "payment_terms_days" in data and not (0 <= data["payment_terms_days"] <= 90):
        raise HTTPException(422, "Payment terms must be 0-90 days")
    for k, v in data.items():
        setattr(p, k, v)
    p.updated_by_user_id = acc.user.id
    write_audit(db, tenant_id=None, user=acc.user, action="platform.pricing_saved", details=data, request=request)
    db.commit()
    return billing.pricing_dict(p)


class TenantBillingIn(BaseModel):
    billing_active: bool | None = None
    billing_start: date | None = None
    onboarding_fee: float | None = None
    subscription_rate_pct: float | None = None
    subscription_minimum: float | None = None
    branch_fee: float | None = None
    branches_included: int | None = None
    onboarding_invoiced: bool | None = None
    billing_email: str | None = None
    notes: str | None = None
    clear: list[str] | None = None       # own prices to remove (use the standard)


@router.get("/billing/dcps")
def billing_dcps(acc: PlatformAccess = Depends(require_platform("platform.revenue")),
                 db: Session = Depends(get_db)):
    """Every DCP this person sees, with its billing set-up and what it owes."""
    out = []
    for t in visible_tenants(acc, db):
        d = billing.billing_dict(db, t.id)
        unpaid = (db.query(PlatformInvoice).filter(PlatformInvoice.tenant_id == t.id,
                                                   PlatformInvoice.status.in_(("issued", "part_paid"))).all())
        d.update({"name": t.name, "code": t.code, "active": t.active,
                  "book": metrics.loan_book(db, t.id)["outstanding"],
                  "unpaid": round(sum(float(i.total) - float(i.paid) for i in unpaid), 2),
                  "unpaid_count": len(unpaid)})
        out.append(d)
    db.commit()
    return {"items": out, "pricing": billing.pricing_dict(billing.pricing(db))}


@router.put("/tenants/{tenant_id}/billing")
def put_tenant_billing(tenant_id: int, body: TenantBillingIn, request: Request,
                       acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
                       db: Session = Depends(get_db)):
    check_tenant(acc, db, tenant_id)
    b = billing.billing_for(db, tenant_id)
    was_started = bool(b.billing_start)
    data = body.model_dump(exclude_unset=True, exclude={"clear"})
    if data.get("billing_active") is None:
        data.pop("billing_active", None)
    if data.get("onboarding_invoiced") is None:
        data.pop("onboarding_invoiced", None)
    for k, v in data.items():
        if isinstance(v, str):
            v = v.strip() or None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
            raise HTTPException(422, f"{k} cannot be negative")
        setattr(b, k, v)
    for k in body.clear or []:
        if k in ("onboarding_fee", "subscription_rate_pct", "subscription_minimum", "branch_fee",
                 "branches_included"):
            setattr(b, k, None)
    if b.billing_active and not was_started:
        start = b.billing_start
        b.billing_start = None
        billing.switch_on(db, b, start)
    b.updated_by_user_id = acc.user.id
    write_audit(db, tenant_id=tenant_id, user=acc.user, action="platform.billing_saved",
                details={k: str(v) for k, v in data.items()}, request=request)
    db.commit()
    return billing.billing_dict(db, tenant_id)


@router.get("/invoices")
def list_invoices(tenant_id: int | None = None, status: str | None = None,
                  acc: PlatformAccess = Depends(require_platform("platform.revenue")),
                  db: Session = Depends(get_db)):
    q = acc.tenant_filter(db.query(PlatformInvoice), PlatformInvoice.tenant_id)
    if tenant_id:
        q = q.filter(PlatformInvoice.tenant_id == tenant_id)
    if status == "unpaid":
        q = q.filter(PlatformInvoice.status.in_(("issued", "part_paid")))
    elif status:
        q = q.filter(PlatformInvoice.status == status)
    return {"items": [billing.invoice_dict(db, i) for i in
                      q.order_by(PlatformInvoice.period_start.desc(), PlatformInvoice.id.desc()).limit(500)]}


class GenerateIn(BaseModel):
    period: str
    tenant_id: int | None = None


@router.post("/invoices/generate")
def generate_invoices(body: GenerateIn, request: Request,
                      acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
                      db: Session = Depends(get_db)):
    from app.models import TenantBilling
    if body.tenant_id:
        check_tenant(acc, db, body.tenant_id)
        targets = [body.tenant_id]
    else:
        q = db.query(TenantBilling.tenant_id).filter(TenantBilling.billing_active == True)  # noqa: E712
        targets = [tid for (tid,) in q.all() if acc.sees(tid)]
    made, problems = [], []
    for tid in targets:
        try:
            inv = billing.generate(db, tid, body.period, acc.user.id)
            made.append(billing.invoice_dict(db, inv))
        except billing.BillingError as exc:
            t = db.get(Tenant, tid)
            problems.append(f"{t.name if t else tid}: {exc}")
    write_audit(db, tenant_id=None, user=acc.user, action="platform.invoices_generated",
                details={"period": body.period, "made": [m["number"] for m in made]}, request=request)
    db.commit()
    if body.tenant_id and problems:
        raise HTTPException(422, problems[0].split(": ", 1)[-1])
    return {"made": made, "problems": problems}


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int, acc: PlatformAccess = Depends(require_platform("platform.revenue")),
                db: Session = Depends(get_db)):
    inv = db.get(PlatformInvoice, invoice_id)
    if inv is None or not acc.sees(inv.tenant_id):
        raise HTTPException(404, "Invoice not found")
    out = billing.invoice_dict(db, inv, lines=True)
    from app.core.config import settings
    out["pay_to"] = {"paybill": settings.FINYL_PAYBILL or None, "account": inv.number}
    b = billing.billing_for(db, inv.tenant_id)
    out["billing_email"] = b.billing_email
    return out


class PaymentIn(BaseModel):
    amount: float
    reference: str
    paid_at: datetime | None = None
    channel: str = "manual"
    note: str | None = None


@router.post("/invoices/{invoice_id}/payments")
def pay_invoice(invoice_id: int, body: PaymentIn, request: Request,
                acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
                db: Session = Depends(get_db)):
    inv = db.get(PlatformInvoice, invoice_id)
    if inv is None or not acc.sees(inv.tenant_id):
        raise HTTPException(404, "Invoice not found")
    try:
        billing.record_payment(db, inv, body.amount, body.reference, body.paid_at or datetime.utcnow(),
                               body.channel, body.note, acc.user.id)
    except billing.BillingError as exc:
        raise HTTPException(422, str(exc))
    write_audit(db, tenant_id=inv.tenant_id, user=acc.user, action="platform.invoice_paid",
                entity_type="platform_invoice", entity_id=inv.id,
                details={"amount": body.amount, "reference": body.reference}, request=request)
    db.commit()
    return billing.invoice_dict(db, inv, lines=True)


class VoidIn(BaseModel):
    reason: str


@router.post("/invoices/{invoice_id}/void")
def void_invoice(invoice_id: int, body: VoidIn, request: Request,
                 acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
                 db: Session = Depends(get_db)):
    inv = db.get(PlatformInvoice, invoice_id)
    if inv is None or not acc.sees(inv.tenant_id):
        raise HTTPException(404, "Invoice not found")
    if float(inv.paid) > 0:
        raise HTTPException(409, "Money was already received on this invoice; it cannot be cancelled.")
    if len((body.reason or "").strip()) < 5:
        raise HTTPException(422, "Give the reason for cancelling")
    from app.models import PlatformInvoiceLine
    kinds = {k for (k,) in db.query(PlatformInvoiceLine.kind).filter(PlatformInvoiceLine.invoice_id == inv.id)}
    b = billing.billing_for(db, inv.tenant_id)
    if "onboarding" in kinds:
        b.onboarding_invoiced = False
    if "branch" in kinds:
        qty = sum(float(q) for (q,) in db.query(PlatformInvoiceLine.quantity)
                  .filter(PlatformInvoiceLine.invoice_id == inv.id, PlatformInvoiceLine.kind == "branch"))
        b.branches_billed = max(0, (b.branches_billed or 0) - int(qty))
    inv.status, inv.void_reason = "void", body.reason.strip()
    write_audit(db, tenant_id=inv.tenant_id, user=acc.user, action="platform.invoice_void",
                entity_type="platform_invoice", entity_id=inv.id, details={"reason": body.reason},
                request=request)
    db.commit()
    return billing.invoice_dict(db, inv)


@router.get("/revenue")
def revenue(months: int = 12, acc: PlatformAccess = Depends(require_platform("platform.revenue")),
            db: Session = Depends(get_db)):
    ids = _ids(acc, db)
    return {"months": billing.revenue(db, ids, max(1, min(months, 36)))}


@router.get("/unmatched")
def unmatched(acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
              db: Session = Depends(get_db)):
    if not acc.all_tenants:
        return {"items": []}           # not tied to a DCP: only for people who see every DCP
    return {"items": [{"id": u.id, "reference": u.reference, "amount": float(u.amount),
                       "account_entered": u.account_entered, "payer": u.payer, "status": u.status,
                       "paid_at": u.paid_at.isoformat() + "Z" if u.paid_at else None}
                      for u in db.query(PlatformUnmatchedPayment).order_by(PlatformUnmatchedPayment.id.desc())
                      .limit(200)]}


class AllocateIn(BaseModel):
    invoice_id: int | None = None
    facility_id: int | None = None


@router.post("/unmatched/{item_id}/allocate")
def allocate(item_id: int, body: AllocateIn, request: Request,
             acc: PlatformAccess = Depends(require_platform("platform.billing_manage")),
             db: Session = Depends(get_db)):
    u = db.get(PlatformUnmatchedPayment, item_id)
    if u is None or u.status != "open" or not acc.all_tenants:
        raise HTTPException(404, "Payment not found")
    try:
        if body.invoice_id:
            inv = db.get(PlatformInvoice, body.invoice_id)
            if inv is None:
                raise HTTPException(404, "Invoice not found")
            billing.record_payment(db, inv, u.amount, u.reference, u.paid_at or datetime.utcnow(),
                                   "mpesa_paybill", f"Allocated from unmatched by {acc.user.email}",
                                   acc.user.id)
            tid = inv.tenant_id
        elif body.facility_id:
            fac = db.get(PlatformFacility, body.facility_id)
            if fac is None:
                raise HTTPException(404, "Facility not found")
            lending.repay(db, fac, u.amount, u.reference, u.paid_at or datetime.utcnow(), "mpesa_paybill",
                          f"Allocated from unmatched by {acc.user.email}", acc.user.id)
            tid = fac.tenant_id
        else:
            raise HTTPException(422, "Choose an invoice or a facility")
    except (billing.BillingError, lending.LendingError) as exc:
        raise HTTPException(422, str(exc))
    u.status, u.resolved_note = "allocated", f"by {acc.user.email}"
    write_audit(db, tenant_id=tid, user=acc.user, action="platform.payment_allocated",
                details={"reference": u.reference}, request=request)
    db.commit()
    return {"status": "allocated"}


# --------------------------------------------------------------------------- #
# Finyl's loans to DCPs
# --------------------------------------------------------------------------- #
@router.get("/facilities")
def list_facilities(acc: PlatformAccess = Depends(require_platform("platform.lending")),
                    db: Session = Depends(get_db)):
    facs = acc.tenant_filter(db.query(PlatformFacility), PlatformFacility.tenant_id).order_by(
        PlatformFacility.id).all()
    return {"items": [lending.facility_dict(db, f) for f in facs]}


@router.post("/facilities")
def create_facility(body: dict, request: Request,
                    acc: PlatformAccess = Depends(require_platform("platform.lending_manage")),
                    db: Session = Depends(get_db)):
    tid = int(body.get("tenant_id") or 0)
    check_tenant(acc, db, tid)
    try:
        fac = lending.create(db, tid, body, acc.user.id)
    except lending.LendingError as exc:
        raise HTTPException(422, str(exc))
    write_audit(db, tenant_id=tid, user=acc.user, action="platform.facility_created",
                entity_type="platform_facility", entity_id=fac.id,
                details={"number": fac.number, "limit": float(fac.limit_amount)}, request=request)
    db.commit()
    return lending.facility_dict(db, fac, detail=True)


def _facility(acc, db, facility_id) -> PlatformFacility:
    fac = db.get(PlatformFacility, facility_id)
    if fac is None or not acc.sees(fac.tenant_id):
        raise HTTPException(404, "Facility not found")
    return fac


@router.get("/facilities/{facility_id}")
def get_facility(facility_id: int, acc: PlatformAccess = Depends(require_platform("platform.lending")),
                 db: Session = Depends(get_db)):
    return lending.facility_dict(db, _facility(acc, db, facility_id), detail=True)


@router.put("/facilities/{facility_id}")
def update_facility(facility_id: int, body: dict, request: Request,
                    acc: PlatformAccess = Depends(require_platform("platform.lending_manage")),
                    db: Session = Depends(get_db)):
    fac = _facility(acc, db, facility_id)
    if "status" in body:
        if body["status"] not in ("active", "suspended", "closed"):
            raise HTTPException(422, "Status must be active, suspended or closed")
        fac.status = body["status"]
    if "limit_amount" in body:
        new = lending._d(body["limit_amount"])
        if new < lending._d(lending.position(db, fac)["principal_outstanding"]):
            raise HTTPException(422, "The limit cannot be below what is already drawn and unpaid")
        fac.limit_amount = new
    for k in ("purpose", "notes"):
        if k in body:
            setattr(fac, k, body[k])
    write_audit(db, tenant_id=fac.tenant_id, user=acc.user, action="platform.facility_updated",
                entity_type="platform_facility", entity_id=fac.id,
                details={k: str(v) for k, v in body.items()}, request=request)
    db.commit()
    return lending.facility_dict(db, fac, detail=True)


class DrawdownIn(BaseModel):
    amount: float
    disbursed_on: date | None = None
    reference: str | None = None
    note: str | None = None


@router.post("/facilities/{facility_id}/drawdowns")
def add_drawdown(facility_id: int, body: DrawdownIn, request: Request,
                 acc: PlatformAccess = Depends(require_platform("platform.lending_manage")),
                 db: Session = Depends(get_db)):
    fac = _facility(acc, db, facility_id)
    try:
        dd = lending.drawdown(db, fac, body.amount, body.disbursed_on or metrics.today_kenya(),
                              body.reference, body.note, acc.user.id)
    except lending.LendingError as exc:
        raise HTTPException(422, str(exc))
    write_audit(db, tenant_id=fac.tenant_id, user=acc.user, action="platform.drawdown",
                entity_type="platform_facility", entity_id=fac.id,
                details={"amount": body.amount, "drawdown_id": dd.id}, request=request)
    db.commit()
    return lending.facility_dict(db, fac, detail=True)


@router.post("/facilities/{facility_id}/payments")
def repay_facility(facility_id: int, body: PaymentIn, request: Request,
                   acc: PlatformAccess = Depends(require_platform("platform.lending_manage")),
                   db: Session = Depends(get_db)):
    fac = _facility(acc, db, facility_id)
    try:
        lending.repay(db, fac, body.amount, body.reference, body.paid_at or datetime.utcnow(),
                      body.channel, body.note, acc.user.id)
    except lending.LendingError as exc:
        raise HTTPException(422, str(exc))
    write_audit(db, tenant_id=fac.tenant_id, user=acc.user, action="platform.facility_repaid",
                entity_type="platform_facility", entity_id=fac.id,
                details={"amount": body.amount, "reference": body.reference}, request=request)
    db.commit()
    return lending.facility_dict(db, fac, detail=True)


# --------------------------------------------------------------------------- #
# Sales pipeline
# --------------------------------------------------------------------------- #
def _lead_dict(db, lead: PlatformLead) -> dict:
    owner = db.get(User, lead.owner_user_id) if lead.owner_user_id else None
    t = db.get(Tenant, lead.tenant_id) if lead.tenant_id else None
    return {"id": lead.id, "company_name": lead.company_name, "contact_name": lead.contact_name,
            "contact_phone": lead.contact_phone, "contact_email": lead.contact_email,
            "stage": lead.stage, "stage_label": STAGE_LABELS.get(lead.stage, lead.stage),
            "expected_loan_book": float(lead.expected_loan_book) if lead.expected_loan_book is not None else None,
            "expected_branches": lead.expected_branches, "source": lead.source,
            "next_step": lead.next_step,
            "next_step_date": lead.next_step_date.isoformat() if lead.next_step_date else None,
            "overdue": bool(lead.next_step_date and lead.next_step_date < date.today()
                            and lead.stage not in ("live", "lost")),
            "owner": owner.full_name if owner else None, "owner_user_id": lead.owner_user_id,
            "tenant_id": lead.tenant_id, "tenant": t.name if t else None,
            "lost_reason": lead.lost_reason, "notes": lead.notes,
            "created_at": lead.created_at.isoformat() + "Z" if lead.created_at else None,
            "updated_at": lead.updated_at.isoformat() + "Z" if lead.updated_at else None}


@router.get("/leads")
def list_leads(acc: PlatformAccess = Depends(require_platform("platform.pipeline")),
               db: Session = Depends(get_db)):
    leads = db.query(PlatformLead).order_by(PlatformLead.updated_at.desc()).all()
    by_stage = {s: 0 for s in STAGES}
    value = {s: 0.0 for s in STAGES}
    for lead in leads:
        by_stage[lead.stage] = by_stage.get(lead.stage, 0) + 1
        value[lead.stage] = value.get(lead.stage, 0) + float(lead.expected_loan_book or 0)
    std = billing.pricing(db)
    potential_mrr = sum(max(float(lead.expected_loan_book or 0) * float(std.subscription_rate_pct) / 100,
                            float(std.subscription_minimum))
                        for lead in leads if lead.stage not in ("live", "lost"))
    onboarded = [{"id": t.id, "name": t.name, "active": t.active,
                  "since": t.created_at.isoformat() + "Z" if t.created_at else None}
                 for t in visible_tenants(acc, db)]
    db.commit()
    return {"stages": [{"key": s, "label": STAGE_LABELS[s], "count": by_stage.get(s, 0),
                        "expected_loan_book": value.get(s, 0)} for s in STAGES],
            "items": [_lead_dict(db, lead) for lead in leads], "onboarded": onboarded,
            "potential_monthly_subscription": round(potential_mrr, 2)}


class LeadIn(BaseModel):
    company_name: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    expected_loan_book: float | None = None
    expected_branches: int | None = None
    source: str | None = None
    next_step: str | None = None
    next_step_date: date | None = None
    owner_user_id: int | None = None
    tenant_id: int | None = None
    notes: str | None = None


@router.post("/leads")
def create_lead(body: LeadIn, request: Request,
                acc: PlatformAccess = Depends(require_platform("platform.pipeline_manage")),
                db: Session = Depends(get_db)):
    if not (body.company_name or "").strip():
        raise HTTPException(422, "Enter the company name")
    lead = PlatformLead(**{k: v for k, v in body.model_dump().items() if v is not None},
                        stage="lead", created_by_user_id=acc.user.id)
    lead.company_name = lead.company_name.strip()[:160]
    if not lead.owner_user_id:
        lead.owner_user_id = acc.user.id
    db.add(lead)
    db.flush()
    db.add(PlatformLeadEvent(lead_id=lead.id, to_stage="lead", note="Added", user_id=acc.user.id))
    write_audit(db, tenant_id=None, user=acc.user, action="platform.lead_created",
                entity_type="platform_lead", entity_id=lead.id, request=request)
    db.commit()
    return _lead_dict(db, lead)


@router.put("/leads/{lead_id}")
def update_lead(lead_id: int, body: LeadIn,
                acc: PlatformAccess = Depends(require_platform("platform.pipeline_manage")),
                db: Session = Depends(get_db)):
    lead = db.get(PlatformLead, lead_id)
    if lead is None:
        raise HTTPException(404, "Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "tenant_id" and v is not None and db.get(Tenant, v) is None:
            raise HTTPException(422, "DCP not found")
        setattr(lead, k, v)
    lead.updated_at = datetime.utcnow()
    db.commit()
    return _lead_dict(db, lead)


class StageIn(BaseModel):
    stage: str
    note: str | None = None
    lost_reason: str | None = None


@router.post("/leads/{lead_id}/stage")
def move_lead(lead_id: int, body: StageIn, request: Request,
              acc: PlatformAccess = Depends(require_platform("platform.pipeline_manage")),
              db: Session = Depends(get_db)):
    lead = db.get(PlatformLead, lead_id)
    if lead is None:
        raise HTTPException(404, "Not found")
    if body.stage not in STAGES:
        raise HTTPException(422, f"Stage must be one of {', '.join(STAGES)}")
    if body.stage == "lost" and not (body.lost_reason or "").strip():
        raise HTTPException(422, "Say why it was lost")
    if body.stage == "live" and not lead.tenant_id:
        raise HTTPException(422, "Link the DCP on Finyl first (edit the lead and choose it)")
    db.add(PlatformLeadEvent(lead_id=lead.id, from_stage=lead.stage, to_stage=body.stage,
                             note=body.note, user_id=acc.user.id))
    lead.stage = body.stage
    if body.stage == "lost":
        lead.lost_reason = body.lost_reason.strip()[:200]
    lead.updated_at = datetime.utcnow()
    write_audit(db, tenant_id=lead.tenant_id, user=acc.user, action="platform.lead_moved",
                entity_type="platform_lead", entity_id=lead.id, details={"to": body.stage}, request=request)
    db.commit()
    return _lead_dict(db, lead)


@router.get("/leads/{lead_id}/history")
def lead_history(lead_id: int, acc: PlatformAccess = Depends(require_platform("platform.pipeline")),
                 db: Session = Depends(get_db)):
    rows = (db.query(PlatformLeadEvent).filter(PlatformLeadEvent.lead_id == lead_id)
            .order_by(PlatformLeadEvent.id).all())
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_({r.user_id for r in rows} or {-1}))}
    return {"items": [{"from": r.from_stage, "to": r.to_stage, "note": r.note, "by": names.get(r.user_id),
                       "at": r.created_at.isoformat() + "Z"} for r in rows]}


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
@router.get("/alerts")
def list_alerts(status: str = "open", acc: PlatformAccess = Depends(require_platform("platform.alerts")),
                db: Session = Depends(get_db)):
    q = db.query(PlatformAlert)
    if status != "all":
        q = q.filter(PlatformAlert.status == status)
    if not acc.all_tenants:
        q = q.filter(PlatformAlert.tenant_id.in_(sorted(acc.tenant_ids) or [-1]))
    names = {t.id: t.name for t in db.query(Tenant)}
    return {"kinds": alerts_svc.KINDS,
            "items": [{"id": a.id, "tenant_id": a.tenant_id, "tenant": names.get(a.tenant_id) or "Finyl platform",
                       "kind": a.kind, "kind_label": alerts_svc.KINDS.get(a.kind, a.kind),
                       "severity": a.severity, "title": a.title, "detail": a.detail, "count": a.count,
                       "status": a.status, "first_seen": a.first_seen.isoformat() + "Z",
                       "last_seen": a.last_seen.isoformat() + "Z",
                       "notified": a.notified_at is not None, "resolved_note": a.resolved_note}
                      for a in q.order_by(PlatformAlert.last_seen.desc()).limit(300)]}


class AlertActionIn(BaseModel):
    note: str | None = None


def _alert(acc, db, alert_id) -> PlatformAlert:
    a = db.get(PlatformAlert, alert_id)
    if a is None or not (acc.all_tenants or (a.tenant_id and a.tenant_id in acc.tenant_ids)):
        raise HTTPException(404, "Alert not found")
    return a


@router.post("/alerts/{alert_id}/acknowledge")
def ack_alert(alert_id: int, acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
              db: Session = Depends(get_db)):
    a = _alert(acc, db, alert_id)
    if a.status == "open":
        a.status, a.acknowledged_by_user_id = "acknowledged", acc.user.id
    db.commit()
    return {"status": a.status}


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: AlertActionIn,
                  acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                  db: Session = Depends(get_db)):
    a = _alert(acc, db, alert_id)
    a.status, a.resolved_at = "resolved", datetime.utcnow()
    a.resolved_note = (body.note or "").strip()[:500] or f"Resolved by {acc.user.email}"
    db.commit()
    return {"status": a.status}


class RecipientIn(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    min_severity: str = "warning"
    kinds: list[str] = []
    tenant_ids: list[int] = []
    weekly_summary: bool = True
    active: bool = True


def _recipient_dict(r: PlatformAlertRecipient) -> dict:
    return {"id": r.id, "name": r.name, "email": r.email, "phone": r.phone, "min_severity": r.min_severity,
            "kinds": r.kinds or [], "tenant_ids": r.tenant_ids or [],
            "weekly_summary": bool(r.weekly_summary), "active": r.active}


def _check_recipient(body: RecipientIn):
    import re
    if not body.name.strip():
        raise HTTPException(422, "Enter a name")
    if not (body.email or body.phone):
        raise HTTPException(422, "Enter an email address or a phone number")
    if body.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", body.email.strip()):
        raise HTTPException(422, "The email address does not look right")
    if body.min_severity not in alerts_svc.SEVERITIES:
        raise HTTPException(422, "Lowest severity must be info, warning or critical")
    bad = [k for k in body.kinds if k not in alerts_svc.KINDS]
    if bad:
        raise HTTPException(422, f"Unknown alert kind: {bad[0]}")


@router.get("/alert-recipients")
def list_recipients(acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                    db: Session = Depends(get_db)):
    return {"items": [_recipient_dict(r) for r in db.query(PlatformAlertRecipient).order_by(PlatformAlertRecipient.id)],
            "kinds": alerts_svc.KINDS, "severities": list(alerts_svc.SEVERITIES)}


@router.post("/alert-recipients")
def add_recipient(body: RecipientIn, request: Request,
                  acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                  db: Session = Depends(get_db)):
    _check_recipient(body)
    r = PlatformAlertRecipient(name=body.name.strip()[:120], email=(body.email or "").strip() or None,
                               phone=(body.phone or "").strip()[:20] or None, min_severity=body.min_severity,
                               kinds=body.kinds, tenant_ids=body.tenant_ids,
                               weekly_summary=body.weekly_summary, active=body.active)
    db.add(r)
    db.flush()
    write_audit(db, tenant_id=None, user=acc.user, action="platform.alert_recipient_added",
                entity_type="alert_recipient", entity_id=r.id, request=request)
    db.commit()
    return _recipient_dict(r)


@router.put("/alert-recipients/{rid}")
def update_recipient(rid: int, body: RecipientIn,
                     acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                     db: Session = Depends(get_db)):
    r = db.get(PlatformAlertRecipient, rid)
    if r is None:
        raise HTTPException(404, "Not found")
    _check_recipient(body)
    r.name, r.email = body.name.strip()[:120], (body.email or "").strip() or None
    r.phone, r.min_severity = (body.phone or "").strip()[:20] or None, body.min_severity
    r.kinds, r.tenant_ids, r.active = body.kinds, body.tenant_ids, body.active
    r.weekly_summary = body.weekly_summary
    db.commit()
    return _recipient_dict(r)


@router.delete("/alert-recipients/{rid}")
def delete_recipient(rid: int, acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                     db: Session = Depends(get_db)):
    r = db.get(PlatformAlertRecipient, rid)
    if r is not None:
        db.delete(r)
        db.commit()
    return {"status": "deleted"}


@router.post("/alert-recipients/{rid}/test")
def test_recipient(rid: int, acc: PlatformAccess = Depends(require_platform("platform.alerts_manage")),
                   db: Session = Depends(get_db)):
    from app.services import mailer
    r = db.get(PlatformAlertRecipient, rid)
    if r is None:
        raise HTTPException(404, "Not found")
    if not r.email:
        raise HTTPException(422, "This recipient has no email address")
    if not mailer.is_configured():
        raise HTTPException(400, "Email is not set up on this server (SES or SMTP)")
    ok = mailer.send_email(r.email, "[Finyl] Test alert",
                           f"This is a test from the Finyl command centre, sent by {acc.user.email}.\n"
                           "Alerts that match your filters will arrive like this.")
    return {"sent": bool(ok)}


# --------------------------------------------------------------------------- #
# Finyl staff and partners
# --------------------------------------------------------------------------- #
def _platform_user_dict(db, u: User) -> dict:
    acc = access_for(db, u)
    names = {t.id: t.name for t in db.query(Tenant)}
    return {"id": u.id, "email": u.email, "full_name": u.full_name, "phone": u.phone,
            "active": u.active, **acc.as_dict(),
            "tenants": [names.get(t) for t in sorted(acc.tenant_ids) if t in names]}


@router.get("/users")
def list_platform_users(acc: PlatformAccess = Depends(require_platform("platform.users_manage")),
                        db: Session = Depends(get_db)):
    users = db.query(User).filter(User.role == PLATFORM_ROLE).order_by(User.full_name).all()
    return {"items": [_platform_user_dict(db, u) for u in users],
            "presets": {k: {"label": v["label"], "permissions": v["permissions"]} for k, v in PRESETS.items()},
            "catalog": PLATFORM_PERMISSIONS,
            "tenants": [{"id": t.id, "name": t.name} for t in visible_tenants(acc, db)]}


class PlatformUserIn(BaseModel):
    email: str | None = None
    full_name: str | None = None
    phone: str | None = None
    password: str | None = None
    title: str | None = None
    preset: str = "custom"
    permissions: list[str] | None = None
    all_tenants: bool = False
    tenant_ids: list[int] = []
    active: bool = True


def _check_access(db, acc: PlatformAccess, body: PlatformUserIn) -> tuple[list[str], list[int]]:
    """Validate the switches and DCPs before anything is written."""
    if body.preset not in PRESETS:
        raise HTTPException(422, "Unknown preset")
    perms = set(body.permissions if body.permissions is not None else PRESETS[body.preset]["permissions"])
    unknown = perms - set(PLATFORM_PERMISSIONS)
    if unknown:
        raise HTTPException(422, f"Unknown switch: {sorted(unknown)[0]}")
    ids = sorted({int(t) for t in body.tenant_ids})
    # Nobody can hand out more than they have themselves.
    if not acc.is_super:
        if perms - acc.permissions or "platform.users_manage" in perms:
            raise HTTPException(403, "You can only give switches you have (and not user management)")
        if not acc.all_tenants and (body.all_tenants or set(ids) - acc.tenant_ids):
            raise HTTPException(403, "You can only assign DCPs you can see yourself")
    for t in ids:
        if db.get(Tenant, t) is None:
            raise HTTPException(422, f"DCP {t} not found")
    return sorted(perms), ids


def _write_access(db, acc: PlatformAccess, user: User, body: PlatformUserIn, perms, ids):
    row = db.get(PlatformUserAccess, user.id)
    if row is None:
        row = PlatformUserAccess(user_id=user.id)
        db.add(row)
    row.preset, row.title = body.preset, (body.title or "").strip()[:80] or None
    row.permissions, row.all_tenants, row.tenant_ids = perms, bool(body.all_tenants), ids
    row.updated_by_user_id = acc.user.id


@router.post("/users")
def create_platform_user(body: PlatformUserIn, request: Request,
                         acc: PlatformAccess = Depends(require_platform("platform.users_manage")),
                         db: Session = Depends(get_db)):
    email = (body.email or "").strip().lower()
    if "@" not in email or not (body.full_name or "").strip():
        raise HTTPException(422, "Enter the person's name and email address")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(409, "A user with that email already exists")
    if not body.password or len(body.password) < 10:
        raise HTTPException(422, "Give a temporary password of at least 10 characters; "
                                 "they must change it when they first sign in")
    perms, ids = _check_access(db, acc, body)
    u = User(email=email, full_name=body.full_name.strip()[:120], role=PLATFORM_ROLE, tenant_id=None,
             hashed_password=hash_password(body.password), active=body.active, force_password_reset=True,
             phone=(body.phone or "").strip()[:20] or None)
    db.add(u)
    db.flush()
    _write_access(db, acc, u, body, perms, ids)
    write_audit(db, tenant_id=None, user=acc.user, action="platform.user_created", entity_type="user",
                entity_id=u.id, details={"email": email, "preset": body.preset,
                                         "all_tenants": body.all_tenants, "tenant_ids": body.tenant_ids},
                request=request)
    db.commit()
    return _platform_user_dict(db, u)


@router.put("/users/{user_id}")
def update_platform_user(user_id: int, body: PlatformUserIn, request: Request,
                         acc: PlatformAccess = Depends(require_platform("platform.users_manage")),
                         db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None or u.role != PLATFORM_ROLE:
        raise HTTPException(404, "User not found")
    if u.id == acc.user.id and not acc.is_super:
        raise HTTPException(403, "You cannot change your own access")
    if body.password and len(body.password) < 10:
        raise HTTPException(422, "The temporary password must be at least 10 characters")
    perms, ids = _check_access(db, acc, body)
    if body.full_name:
        u.full_name = body.full_name.strip()[:120]
    if body.phone is not None:
        u.phone = body.phone.strip()[:20] or None
    if not body.active and u.active:
        u.token_version = (u.token_version or 0) + 1      # signs them out now
    u.active = body.active
    if body.password:
        u.hashed_password, u.force_password_reset = hash_password(body.password), True
        u.token_version = (u.token_version or 0) + 1
    _write_access(db, acc, u, body, perms, ids)
    write_audit(db, tenant_id=None, user=acc.user, action="platform.user_updated", entity_type="user",
                entity_id=u.id, details={"preset": body.preset, "permissions": body.permissions,
                                         "all_tenants": body.all_tenants, "tenant_ids": body.tenant_ids,
                                         "active": body.active}, request=request)
    db.commit()
    return _platform_user_dict(db, u)
