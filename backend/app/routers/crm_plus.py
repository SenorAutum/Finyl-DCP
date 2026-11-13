"""WP17 — CRM expansion: client timeline, contact log, follow-up tasks,
segments, SMS campaigns (maker-checker) and the pipeline report.

/api/v1/crm-plus/...   Module 'crm'.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import UserScope, get_scope, require_module, require_permission, write_audit
from app.core.money import money
from app.models import (Borrower, CrmCampaign, CrmCampaignRecipient, CrmInteraction, CrmLead, CrmSegment, CrmTask,
                        Loan, User)
from app.services import crm_plus as C
from app.services import ledger as L
from app.services import sms

router = APIRouter(prefix="/api/v1/crm-plus", tags=["crm-plus"])
MODULE = require_module("crm")

KINDS = ("call", "visit", "sms", "whatsapp", "meeting", "email", "note")
OUTCOMES = ("reached", "no_answer", "interested", "not_interested", "promise", "complaint", "other")
PRIORITIES = ("low", "normal", "high")


def perm(*keys, mode="all"):
    return require_permission(*keys, mode=mode)


def _can(db, user, key) -> bool:
    if user.role == "super_admin":
        return True
    from app.services.authz import effective_permissions
    return key in effective_permissions(db, user.tenant_id, user.role)


def _d(v, field="date"):
    try:
        return datetime.strptime(str(v), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, f"{field} must be a date (YYYY-MM-DD)")


def _client(db, tenant_id, cid, scope: UserScope) -> Borrower:
    b = db.query(Borrower).filter(Borrower.id == cid, Borrower.tenant_id == tenant_id).first()
    if b is None or not scope.can_see_client(b):
        raise HTTPException(404, "Client not found")
    return b


def _lead(db, tenant_id, lid) -> CrmLead:
    ld = db.query(CrmLead).filter(CrmLead.id == lid, CrmLead.tenant_id == tenant_id).first()
    if ld is None:
        raise HTTPException(404, "Lead not found")
    return ld


def _names(db, ids):
    ids = {i for i in ids if i}
    return {u.id: u.full_name for u in db.query(User).filter(User.id.in_(ids or {-1}))}


def _iso(v):
    return v.isoformat() if v else None


# =========================================================================== options
@router.get("/options")
def options(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), user: User = Depends(perm("crm.view"))):
    """Lists for the forms: kinds, outcomes, staff to assign tasks to, and what this user may do."""
    staff = db.query(User).filter(User.tenant_id == tenant_id, User.active.isnot(False)).order_by(User.full_name).all()
    from app.models import Branch
    return {"kinds": KINDS, "outcomes": OUTCOMES, "priorities": PRIORITIES, "loan_states": C.LOAN_STATES,
            "users": [{"id": u.id, "name": u.full_name, "role": u.role} for u in staff],
            "branches": [{"id": b.id, "name": b.name} for b in db.query(Branch).filter(Branch.tenant_id == tenant_id).order_by(Branch.name)],
            "me": user.id,
            "can": {k: _can(db, user, k) for k in ("crm.log", "crm.campaigns", "crm.campaigns_approve")}}


@router.get("/clients/search")
def client_search(q: str, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                  _: User = Depends(perm("crm.view")), scope: UserScope = Depends(get_scope)):
    like = f"%{q.strip()}%"
    qry = db.query(Borrower).filter(Borrower.tenant_id == tenant_id,
                                    or_(Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
                                        Borrower.phone.ilike(like)))
    rows = scope.apply_client(qry, Borrower).limit(20).all()
    return {"clients": [{"id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": b.phone} for b in rows]}


# =========================================================================== client 360
@router.get("/clients/{client_id}")
def client_360(client_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               _: User = Depends(perm("crm.view")), scope: UserScope = Depends(get_scope)):
    """One client: headline figures, open tasks and the full timeline."""
    b = _client(db, tenant_id, client_id, scope)
    loans = db.query(Loan).filter(Loan.borrower_id == b.id, Loan.tenant_id == tenant_id).all()
    open_loans = [ln for ln in loans if ln.status in C.OPEN]
    today = L.today()
    tasks = db.query(CrmTask).filter(CrmTask.borrower_id == b.id, CrmTask.status == "open").order_by(CrmTask.due_date).all()
    names = _names(db, [t.assigned_user_id for t in tasks])
    segs = [s for s in db.query(CrmSegment).filter(CrmSegment.tenant_id == tenant_id).order_by(CrmSegment.name)]
    member_of = []
    for s in segs[:30]:
        try:
            if any(x.id == b.id for x in C.evaluate(db, tenant_id, s.rules or {})):
                member_of.append(s.name)
        except HTTPException:
            pass
    from app.models import KycConsent
    consent = db.query(KycConsent).filter(KycConsent.borrower_id == b.id).order_by(KycConsent.consented_at.desc()).first()
    phone = sms.normalise_phone(b.phone or "")
    return {"client": {"id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": b.phone, "gender": b.gender,
                       "sector": b.business_sector, "status": b.profile_status, "since": _iso(b.created_at)},
            "figures": {"loans": len(loans), "repaid": sum(1 for ln in loans if ln.status == "paid"),
                        "open": len(open_loans),
                        "outstanding": float(sum((money(ln.outstanding_balance or 0) for ln in open_loans), L.ZERO)),
                        "in_arrears": any(ln.due_date and ln.due_date < today for ln in open_loans),
                        "cycle": max([ln.loan_cycle_number or 0 for ln in loans] or [0])},
            "marketing_consent": bool(consent and consent.consent_marketing),
            "opted_out": bool(phone and sms.is_opted_out(db, tenant_id, phone)),
            "segments": member_of,
            "tasks": [_task_dict(t, names) for t in tasks],
            "timeline": C.timeline(db, tenant_id, b)}


# =========================================================================== interactions
class NextStep(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    due_date: str
    assigned_user_id: int | None = None
    priority: str = "normal"


class InteractionIn(BaseModel):
    borrower_id: int | None = None
    lead_id: int | None = None
    kind: str
    direction: str = "out"
    subject: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    outcome: str | None = None
    happened_at: str | None = None
    next_step: NextStep | None = None


@router.post("/interactions")
def log_interaction(body: InteractionIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("crm.log")), scope: UserScope = Depends(get_scope)):
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(KINDS)}")
    if body.direction not in ("in", "out"):
        raise HTTPException(422, "direction must be in or out")
    if body.outcome and body.outcome not in OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {', '.join(OUTCOMES)}")
    if bool(body.borrower_id) == bool(body.lead_id):
        raise HTTPException(422, "Choose a client or a lead (one of them)")
    if body.borrower_id:
        _client(db, tenant_id, body.borrower_id, scope)
    else:
        _lead(db, tenant_id, body.lead_id)
    when = datetime.utcnow()
    if body.happened_at:
        try:
            when = datetime.fromisoformat(body.happened_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "happened_at must be a date and time")
        if when.tzinfo is not None:
            from datetime import timezone
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        if when > datetime.utcnow():
            raise HTTPException(422, "Contact cannot be in the future")
    if not (body.notes or body.subject):
        raise HTTPException(422, "Write a subject or a note")
    i = CrmInteraction(tenant_id=tenant_id, borrower_id=body.borrower_id, lead_id=body.lead_id, kind=body.kind,
                       direction=body.direction, subject=body.subject, notes=body.notes, outcome=body.outcome,
                       happened_at=when, user_id=user.id)
    db.add(i)
    db.flush()
    task = None
    if body.next_step:
        task = _make_task(db, tenant_id, user, title=body.next_step.title, due_date=body.next_step.due_date,
                          assigned_user_id=body.next_step.assigned_user_id or user.id, priority=body.next_step.priority,
                          borrower_id=body.borrower_id, lead_id=body.lead_id, interaction_id=i.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.interaction_logged", entity_type="borrower" if body.borrower_id else "crm_lead",
                entity_id=body.borrower_id or body.lead_id, details={"kind": body.kind, "outcome": body.outcome}, request=request)
    db.commit()
    return {"id": i.id, "task_id": task.id if task else None}


@router.get("/leads/{lead_id}/interactions")
def lead_interactions(lead_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                      _: User = Depends(perm("crm.view"))):
    _lead(db, tenant_id, lead_id)
    rows = db.query(CrmInteraction).filter(CrmInteraction.lead_id == lead_id).order_by(CrmInteraction.happened_at.desc()).all()
    tasks = db.query(CrmTask).filter(CrmTask.lead_id == lead_id).order_by(CrmTask.due_date).all()
    names = _names(db, [r.user_id for r in rows] + [t.assigned_user_id for t in tasks])
    return {"interactions": [{"id": r.id, "kind": r.kind, "direction": r.direction, "subject": r.subject, "notes": r.notes,
                              "outcome": r.outcome, "happened_at": _iso(r.happened_at), "by": names.get(r.user_id)} for r in rows],
            "tasks": [_task_dict(t, names) for t in tasks]}


# =========================================================================== tasks
def _task_dict(t: CrmTask, names=None, extra=None):
    d = {"id": t.id, "title": t.title, "notes": t.notes, "due_date": t.due_date.isoformat(), "priority": t.priority,
         "status": t.status, "assigned_user_id": t.assigned_user_id, "assigned_to": (names or {}).get(t.assigned_user_id),
         "borrower_id": t.borrower_id, "lead_id": t.lead_id, "loan_id": t.loan_id,
         "overdue": t.status == "open" and t.due_date < L.today(), "completed_at": _iso(t.completed_at),
         "completion_note": t.completion_note, "created_by": (names or {}).get(t.created_by_user_id)}
    d.update(extra or {})
    return d


def _make_task(db, tenant_id, user, *, title, due_date, assigned_user_id, priority="normal", notes=None,
               borrower_id=None, lead_id=None, loan_id=None, interaction_id=None) -> CrmTask:
    if priority not in PRIORITIES:
        raise HTTPException(422, f"priority must be one of {', '.join(PRIORITIES)}")
    due = _d(due_date, "due_date")
    who = db.query(User).filter(User.id == assigned_user_id, User.tenant_id == tenant_id).first()
    if who is None:
        raise HTTPException(422, "Choose a member of staff to do this")
    if loan_id:
        ln = db.query(Loan).filter(Loan.id == loan_id, Loan.tenant_id == tenant_id).first()
        if ln is None:
            raise HTTPException(422, "Loan not found")
        borrower_id = borrower_id or ln.borrower_id
    t = CrmTask(tenant_id=tenant_id, title=title, notes=notes, due_date=due, priority=priority, assigned_user_id=who.id,
                borrower_id=borrower_id, lead_id=lead_id, loan_id=loan_id, interaction_id=interaction_id,
                created_by_user_id=user.id)
    db.add(t)
    db.flush()
    return t


class TaskIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    notes: str | None = Field(default=None, max_length=5000)
    due_date: str
    priority: str = "normal"
    assigned_user_id: int | None = None
    borrower_id: int | None = None
    lead_id: int | None = None
    loan_id: int | None = None


@router.get("/tasks")
def tasks(mine: bool = True, status: str | None = "open", assigned_user_id: int | None = None,
          tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), user: User = Depends(perm("crm.view"))):
    q = db.query(CrmTask).filter(CrmTask.tenant_id == tenant_id)
    if mine:
        q = q.filter(CrmTask.assigned_user_id == user.id)
    elif assigned_user_id:
        q = q.filter(CrmTask.assigned_user_id == assigned_user_id)
    if status:
        q = q.filter(CrmTask.status == status)
    rows = q.order_by(CrmTask.due_date, CrmTask.id).limit(500).all()
    names = _names(db, [t.assigned_user_id for t in rows] + [t.created_by_user_id for t in rows])
    people = {b.id: f"{b.first_name} {b.last_name}" for b in
              db.query(Borrower).filter(Borrower.id.in_({t.borrower_id for t in rows if t.borrower_id} or {-1}))}
    leads = {ld.id: ld.name for ld in db.query(CrmLead).filter(CrmLead.id.in_({t.lead_id for t in rows if t.lead_id} or {-1}))}
    loans = {ln.id: ln.account_number for ln in db.query(Loan).filter(Loan.id.in_({t.loan_id for t in rows if t.loan_id} or {-1}))}
    out = [_task_dict(t, names, {"client": people.get(t.borrower_id), "lead": leads.get(t.lead_id),
                                  "account_number": loans.get(t.loan_id)}) for t in rows]
    today = L.today()
    return {"tasks": out, "counts": {"overdue": sum(1 for t in out if t["overdue"]),
                                     "today": sum(1 for t in out if t["status"] == "open" and t["due_date"] == today.isoformat()),
                                     "open": sum(1 for t in out if t["status"] == "open")}}


@router.post("/tasks")
def create_task(body: TaskIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                user: User = Depends(perm("crm.log")), scope: UserScope = Depends(get_scope)):
    if body.borrower_id:
        _client(db, tenant_id, body.borrower_id, scope)
    if body.lead_id:
        _lead(db, tenant_id, body.lead_id)
    t = _make_task(db, tenant_id, user, title=body.title, notes=body.notes, due_date=body.due_date, priority=body.priority,
                   assigned_user_id=body.assigned_user_id or user.id, borrower_id=body.borrower_id, lead_id=body.lead_id,
                   loan_id=body.loan_id)
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.task_created", entity_type="crm_task", entity_id=t.id,
                details={"assigned_user_id": t.assigned_user_id, "due": body.due_date}, request=request)
    db.commit()
    return {"id": t.id}


class TaskDone(BaseModel):
    status: str = "done"
    note: str | None = Field(default=None, max_length=2000)


@router.post("/tasks/{task_id}/close")
def close_task(task_id: int, body: TaskDone, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
               user: User = Depends(perm("crm.log"))):
    t = db.query(CrmTask).filter(CrmTask.id == task_id, CrmTask.tenant_id == tenant_id).first()
    if t is None:
        raise HTTPException(404, "Task not found")
    if body.status not in ("done", "cancelled", "open"):
        raise HTTPException(422, "status must be done, cancelled or open")
    if t.assigned_user_id != user.id and t.created_by_user_id != user.id and not _can(db, user, "crm.campaigns_approve"):
        raise HTTPException(403, "Only the person it is assigned to, the person who set it, or a manager can close this task")
    t.status = body.status
    t.completed_at = datetime.utcnow() if body.status != "open" else None
    t.completion_note = body.note
    db.commit()
    return {"id": t.id, "status": t.status}


# =========================================================================== segments
class SegmentIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    rules: dict = {}


def _scoped_rules(rules: dict, scope: UserScope) -> dict:
    """Someone who sees only some branches can only build segments inside them."""
    rules = C.clean_rules(rules)
    if not scope.company_wide and scope.branch_ids is not None:
        mine = set(scope.branch_ids)
        chosen = set(rules.get("branch_ids") or mine)
        rules["branch_ids"] = sorted(chosen & mine) or [-1]
    return rules


def _seg_dict(s: CrmSegment, count=None):
    return {"id": s.id, "name": s.name, "description": s.description, "rules": s.rules or {}, "count": count,
            "created_at": _iso(s.created_at)}


@router.get("/segments")
def segments(tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("crm.view"))):
    rows = db.query(CrmSegment).filter(CrmSegment.tenant_id == tenant_id).order_by(CrmSegment.name).all()
    return {"segments": [_seg_dict(s, len(C.evaluate(db, tenant_id, s.rules or {}))) for s in rows]}


@router.post("/segments/preview")
def preview_segment(body: dict, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    _: User = Depends(perm("crm.view")), scope: UserScope = Depends(get_scope)):
    rules = _scoped_rules(body.get("rules") or {}, scope)
    people = C.evaluate(db, tenant_id, rules)
    return {"count": len(people), "rules": rules,
            "sample": [{"id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": b.phone} for b in people[:50]]}


@router.post("/segments")
def create_segment(body: SegmentIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                   user: User = Depends(perm("crm.campaigns")), scope: UserScope = Depends(get_scope)):
    if db.query(CrmSegment).filter(CrmSegment.tenant_id == tenant_id, func.lower(CrmSegment.name) == body.name.lower()).first():
        raise HTTPException(409, "A segment with this name already exists")
    s = CrmSegment(tenant_id=tenant_id, name=body.name, description=body.description,
                   rules=_scoped_rules(body.rules, scope), created_by_user_id=user.id)
    db.add(s)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.segment_created", entity_type="crm_segment",
                entity_id=s.id, details={"rules": s.rules}, request=request)
    db.commit()
    return _seg_dict(s, len(C.evaluate(db, tenant_id, s.rules)))


@router.put("/segments/{seg_id}")
def update_segment(seg_id: int, body: SegmentIn, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                   user: User = Depends(perm("crm.campaigns")), scope: UserScope = Depends(get_scope)):
    s = db.query(CrmSegment).filter(CrmSegment.id == seg_id, CrmSegment.tenant_id == tenant_id).first()
    if s is None:
        raise HTTPException(404, "Segment not found")
    if db.query(CrmCampaign).filter(CrmCampaign.segment_id == s.id, CrmCampaign.status.in_(("pending", "approved", "sending"))).first():
        raise HTTPException(409, "A campaign waiting to go out uses this segment. Cancel it first, or copy the segment.")
    clash = db.query(CrmSegment).filter(CrmSegment.tenant_id == tenant_id, func.lower(CrmSegment.name) == body.name.lower(),
                                        CrmSegment.id != s.id).first()
    if clash:
        raise HTTPException(409, "A segment with this name already exists")
    s.name, s.description, s.rules = body.name, body.description, _scoped_rules(body.rules, scope)
    db.commit()
    return _seg_dict(s, len(C.evaluate(db, tenant_id, s.rules)))


@router.delete("/segments/{seg_id}")
def delete_segment(seg_id: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                   _: User = Depends(perm("crm.campaigns"))):
    s = db.query(CrmSegment).filter(CrmSegment.id == seg_id, CrmSegment.tenant_id == tenant_id).first()
    if s is None:
        raise HTTPException(404, "Segment not found")
    if db.query(CrmCampaign).filter(CrmCampaign.segment_id == s.id).first():
        raise HTTPException(409, "Campaigns have used this segment, so it is kept for the record")
    db.delete(s)
    db.commit()
    return {"deleted": seg_id}


# =========================================================================== campaigns
class CampaignIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    segment_id: int
    purpose: str = "marketing"
    message: str = Field(min_length=5, max_length=600)
    send_at: str | None = None


def _camp_dict(c: CrmCampaign, names=None, seg=None):
    return {"id": c.id, "name": c.name, "segment_id": c.segment_id, "segment": seg.name if seg else None,
            "purpose": c.purpose, "message": c.message, "status": c.status, "send_at": _iso(c.send_at),
            "created_by_user_id": c.created_by_user_id, "created_by": (names or {}).get(c.created_by_user_id),
            "approved_by": (names or {}).get(c.approved_by_user_id), "approved_at": _iso(c.approved_at),
            "reject_reason": c.reject_reason, "recipients": c.recipients, "sent": c.sent, "failed": c.failed,
            "skipped": c.skipped, "started_at": _iso(c.started_at), "finished_at": _iso(c.finished_at),
            "created_at": _iso(c.created_at)}


def _campaign(db, tenant_id, cid) -> CrmCampaign:
    c = db.query(CrmCampaign).filter(CrmCampaign.id == cid, CrmCampaign.tenant_id == tenant_id).with_for_update().first()
    if c is None:
        raise HTTPException(404, "Campaign not found")
    return c


def _check(db, tenant_id, body: CampaignIn):
    if body.purpose not in ("marketing", "service"):
        raise HTTPException(422, "purpose must be marketing or service")
    seg = db.query(CrmSegment).filter(CrmSegment.id == body.segment_id, CrmSegment.tenant_id == tenant_id).first()
    if seg is None:
        raise HTTPException(422, "Choose a segment")
    send_at = None
    if body.send_at:
        try:
            send_at = datetime.fromisoformat(body.send_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "send_at must be a date and time")
        if send_at.tzinfo is not None:
            from datetime import timezone
            send_at = send_at.astimezone(timezone.utc).replace(tzinfo=None)
    return seg, send_at


@router.get("/campaigns")
def campaigns(status: str | None = None, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
              _: User = Depends(perm("crm.view"))):
    q = db.query(CrmCampaign).filter(CrmCampaign.tenant_id == tenant_id)
    if status:
        q = q.filter(CrmCampaign.status == status)
    rows = q.order_by(CrmCampaign.id.desc()).limit(300).all()
    names = _names(db, [c.created_by_user_id for c in rows] + [c.approved_by_user_id for c in rows])
    segs = {s.id: s for s in db.query(CrmSegment).filter(CrmSegment.tenant_id == tenant_id)}
    return {"campaigns": [_camp_dict(c, names, segs.get(c.segment_id)) for c in rows]}


@router.get("/campaigns/{cid}")
def campaign(cid: int, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db), _: User = Depends(perm("crm.view"))):
    c = db.query(CrmCampaign).filter(CrmCampaign.id == cid, CrmCampaign.tenant_id == tenant_id).first()
    if c is None:
        raise HTTPException(404, "Campaign not found")
    seg = db.get(CrmSegment, c.segment_id)
    names = _names(db, [c.created_by_user_id, c.approved_by_user_id])
    out = _camp_dict(c, names, seg)
    rec = db.query(CrmCampaignRecipient, Borrower).join(Borrower, Borrower.id == CrmCampaignRecipient.borrower_id) \
        .filter(CrmCampaignRecipient.campaign_id == c.id).order_by(CrmCampaignRecipient.id).limit(2000).all()
    out["recipient_list"] = [{"borrower_id": b.id, "name": f"{b.first_name} {b.last_name}", "phone": r.phone, "status": r.status,
                              "reason": r.reason} for r, b in rec]
    if c.status in ("draft", "pending", "approved"):
        people = C.evaluate(db, tenant_id, seg.rules if seg else {})
        out["audience"] = len(people)
        if c.purpose == "marketing":
            ok = C._consented(db, tenant_id, [b.id for b in people])
            out["audience_with_consent"] = len(ok)
            people = [b for b in people if b.id in ok]
        out["sample"] = C.render(db, tenant_id, c.message, people[0], c.purpose) if people else None
    return out


@router.post("/campaigns/preview")
def preview_message(body: dict, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    _: User = Depends(perm("crm.campaigns"))):
    class _B:  # a sample client
        first_name, last_name = "Achieng", "Otieno"
    text = C.render(db, tenant_id, str(body.get("message") or ""), _B(), body.get("purpose") or "marketing")
    return {"text": text, "length": len(text), "parts": (len(text) - 1) // 153 + 1 if len(text) > 160 else 1}


@router.post("/campaigns")
def create_campaign(body: CampaignIn, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("crm.campaigns"))):
    seg, send_at = _check(db, tenant_id, body)
    c = CrmCampaign(tenant_id=tenant_id, name=body.name, segment_id=seg.id, purpose=body.purpose, message=body.message,
                    send_at=send_at, status="draft", created_by_user_id=user.id)
    db.add(c)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.campaign_drafted", entity_type="crm_campaign",
                entity_id=c.id, details={"segment": seg.name, "purpose": c.purpose}, request=request)
    db.commit()
    return {"id": c.id, "status": c.status}


@router.put("/campaigns/{cid}")
def update_campaign(cid: int, body: CampaignIn, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    _: User = Depends(perm("crm.campaigns"))):
    c = _campaign(db, tenant_id, cid)
    if c.status not in ("draft", "rejected"):
        raise HTTPException(409, f"This campaign is {c.status}; only drafts can be changed")
    seg, send_at = _check(db, tenant_id, body)
    c.name, c.segment_id, c.purpose, c.message, c.send_at, c.status = body.name, seg.id, body.purpose, body.message, send_at, "draft"
    db.commit()
    return {"id": c.id, "status": c.status}


@router.post("/campaigns/{cid}/submit")
def submit_campaign(cid: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("crm.campaigns"))):
    c = _campaign(db, tenant_id, cid)
    if c.status not in ("draft", "rejected"):
        raise HTTPException(409, f"This campaign is {c.status}")
    seg = db.get(CrmSegment, c.segment_id)
    n = len(C.evaluate(db, tenant_id, seg.rules if seg else {}))
    if n == 0:
        raise HTTPException(422, "Nobody is in this segment")
    if n > C.MAX_RECIPIENTS:
        raise HTTPException(422, f"This segment has {n:,} clients; one campaign can go to at most {C.MAX_RECIPIENTS:,}. Narrow the segment.")
    c.status, c.reject_reason = "pending", None
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.campaign_submitted", entity_type="crm_campaign",
                entity_id=c.id, details={"audience": n}, request=request)
    db.commit()
    return {"id": c.id, "status": c.status, "audience": n}


class Decision(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=1000)


@router.post("/campaigns/{cid}/decide")
def decide_campaign(cid: int, body: Decision, request: Request, tenant_id: int = Depends(MODULE),
                    db: Session = Depends(get_db), user: User = Depends(perm("crm.campaigns_approve"))):
    c = _campaign(db, tenant_id, cid)
    if c.status != "pending":
        raise HTTPException(409, f"This campaign is {c.status}")
    if c.created_by_user_id == user.id:
        raise HTTPException(403, "Someone other than the person who drafted the campaign must approve it")
    if not body.approve:
        if not body.note or len(body.note.strip()) < 5:
            raise HTTPException(422, "Say why it is rejected")
        c.status, c.reject_reason = "rejected", body.note
        write_audit(db, tenant_id=tenant_id, user=user, action="crm.campaign_rejected", entity_type="crm_campaign",
                    entity_id=c.id, details={"note": body.note}, request=request)
        db.commit()
        return {"id": c.id, "status": c.status}
    c.status, c.approved_by_user_id, c.approved_at = "approved", user.id, datetime.utcnow()
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.campaign_approved", entity_type="crm_campaign",
                entity_id=c.id, details={"send_at": _iso(c.send_at)}, request=request)
    db.flush()
    if c.send_at is None or c.send_at <= datetime.utcnow():
        C.send_campaign(db, c)
    db.commit()
    return {"id": c.id, "status": c.status, "sent": c.sent, "failed": c.failed, "skipped": c.skipped}


@router.post("/campaigns/{cid}/cancel")
def cancel_campaign(cid: int, request: Request, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
                    user: User = Depends(perm("crm.campaigns"))):
    c = _campaign(db, tenant_id, cid)
    if c.status not in ("draft", "pending", "approved", "rejected"):
        raise HTTPException(409, f"This campaign is {c.status}")
    c.status = "cancelled"
    write_audit(db, tenant_id=tenant_id, user=user, action="crm.campaign_cancelled", entity_type="crm_campaign",
                entity_id=c.id, request=request)
    db.commit()
    return {"id": c.id, "status": c.status}


# =========================================================================== pipeline
@router.get("/pipeline")
def pipeline(months: int = 6, tenant_id: int = Depends(MODULE), db: Session = Depends(get_db),
             _: User = Depends(perm("crm.view"))):
    return C.pipeline_report(db, tenant_id, max(1, min(months, 24)))
