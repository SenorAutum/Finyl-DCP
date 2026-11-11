"""WP11: bank statement import — preview, import, saved column layouts.
WP12: also reads the M-Pesa business portal statement (missed paybill payments).

The DCP uploads its bank statement (CSV or .xlsx). Preview shows what would
happen to every line without saving anything. Import reads the file again
(the preview is never trusted), records matched lines as manual payments
(applied below the DCP's manual-payment threshold, otherwise waiting for a
second person), parks the rest in the Suspense Account, and skips lines whose
bank reference is already recorded.
"""
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.models import User
from app.services import bank_import as bs
from app.services import payment_approvals as pay_appr
from app.services import rbac
from app.services import repayments as rp

router = APIRouter(prefix="/api/v1/bank-import", tags=["bank statement import"])

_PERM = "payments.bank_import"


async def _rows(file: UploadFile):
    data = await file.read(bs.MAX_FILE_BYTES + 1)
    try:
        return bs.read_rows(data, file.filename or "")
    except bs.StatementError as exc:
        raise HTTPException(422, str(exc))


def _layout_from(db, tenant_id, layout_json, layout_name):
    if layout_json:
        try:
            layout = json.loads(layout_json)
        except ValueError:
            raise HTTPException(422, "layout is not valid JSON")
        if not isinstance(layout, dict):
            raise HTTPException(422, "layout must be an object")
        return layout
    if layout_name:
        saved = bs.get_layouts(db, tenant_id).get(layout_name)
        if saved is None:
            raise HTTPException(404, f"No saved layout called {layout_name!r}")
        return saved
    return None


@router.get("/layouts")
def list_layouts(tenant_id: int = Depends(require_module("payments")),
                 _: User = Depends(require_permission(_PERM)),
                 db: Session = Depends(get_db)):
    return {"layouts": bs.get_layouts(db, tenant_id)}


@router.delete("/layouts/{name}")
def remove_layout(name: str, request: Request,
                  tenant_id: int = Depends(require_module("payments")),
                  user: User = Depends(require_permission(_PERM)),
                  db: Session = Depends(get_db)):
    layouts = bs.delete_layout(db, tenant_id, user.id, name)
    write_audit(db, tenant_id=tenant_id, user=user, action="payments.bank_layout_deleted",
                entity_type="bank_layout", entity_id=name[:40], request=request)
    db.commit()
    return {"layouts": layouts}


@router.post("/preview")
async def preview(file: UploadFile = File(...), layout: str | None = Form(None),
                  layout_name: str | None = Form(None),
                  tenant_id: int = Depends(require_module("payments")),
                  _: User = Depends(require_permission(_PERM)),
                  db: Session = Depends(get_db)):
    """What importing this file would do. Saves nothing. Without a layout the
    columns are guessed; when they cannot be, the first lines are returned so
    the person can choose the heading row and columns."""
    rows = await _rows(file)
    chosen = _layout_from(db, tenant_id, layout, layout_name)
    detected = chosen is None
    if chosen is None:
        try:
            chosen = bs.detect_layout(rows)
        except bs.StatementError as exc:
            return {"filename": file.filename, "layout": None, "message": str(exc),
                    "first_rows": rows[:15]}
    try:
        lines = bs.analyse(db, tenant_id, rows, chosen)
        h = bs._header_index(rows, bs.clean_layout(chosen))
    except bs.StatementError as exc:
        return {"filename": file.filename, "layout": None, "message": str(exc),
                "first_rows": rows[:15]}
    db.rollback()                       # nothing from a preview is ever kept
    return {"filename": file.filename, "layout": bs.clean_layout(chosen), "detected": detected,
            "columns": [c for c in rows[h] if c], "first_rows": rows[:15],
            "summary": bs.summary(lines), "lines": [ln.as_dict() for ln in lines]}


@router.post("/import")
async def import_statement(request: Request, file: UploadFile = File(...),
                           layout: str | None = Form(None), layout_name: str | None = Form(None),
                           bank_account: str = Form(""), save_as: str | None = Form(None),
                           leave_out: str | None = Form(None),
                           tenant_id: int = Depends(require_module("payments")),
                           user: User = Depends(require_permission(_PERM)),
                           db: Session = Depends(get_db)):
    """Record the statement. `leave_out` lists line numbers (comma separated)
    the person unticked in the preview; those are not recorded at all."""
    rows = await _rows(file)
    chosen = _layout_from(db, tenant_id, layout, layout_name)
    try:
        if chosen is None:
            chosen = bs.detect_layout(rows)
        lines = bs.analyse(db, tenant_id, rows, chosen)
        if save_as:
            bs.save_layout(db, tenant_id, user.id, save_as, chosen)
    except bs.StatementError as exc:
        raise HTTPException(422, str(exc))
    try:
        skip = {int(x) for x in (leave_out or "").replace(" ", "").split(",") if x}
    except ValueError:
        raise HTTPException(422, "leave_out must be line numbers separated by commas")

    label = " ".join((bank_account or "").split())[:60]
    # WP12: an M-Pesa business portal statement records paybill payments.
    mpesa_stmt = bs.clean_layout(chosen).get("kind") == "mpesa"
    channel, source = ("mpesa_paybill", "mpesa_stmt") if mpesa_stmt else ("bank", "bank")
    applied, parked, to_suspense, left_out, outcome = 0, 0, 0, 0, []
    for ln in lines:
        if ln.status not in ("matched", "review"):
            continue
        if ln.line in skip:
            left_out += 1
            outcome.append({"line": ln.line, "result": "left out"})
            continue
        note = (f"{'M-Pesa' if mpesa_stmt else 'Bank'} statement {file.filename or ''}"
                + (f" ({label})" if label else "") + f", line {ln.line}: {ln.description[:80]}")
        if ln.status == "matched":
            loan = ln.match.loan
            details = {"channel": channel, "reference": ln.reference,
                       "paid_at": ln.paid_at.isoformat(), "note": note,
                       "payer_name": ln.payer_name, "payer_phone": ln.payer_phone,
                       "matched_by": ln.match.how, "source_endpoint": "bank_statement"}
            if rbac.requires_maker_checker(db, tenant_id, pay_appr.MANUAL_THRESHOLD, float(ln.amount)):
                p = pay_appr.park_manual_payment(db, tenant_id=tenant_id, user=user, loan=loan,
                                                 amount=ln.amount, details=details,
                                                 reason=note[:200])
                parked += 1
                outcome.append({"line": ln.line, "result": "waiting for approval",
                                "pending_id": p.id, "loan_id": loan.id})
            else:
                res = pay_appr.apply_manual(db, tenant_id=tenant_id, loan_id=loan.id,
                                            amount=ln.amount, details=details,
                                            actor_user_id=user.id)
                applied += 1
                outcome.append({"line": ln.line, "result": "applied", "loan_id": loan.id,
                                "applied": res.get("applied")})
        else:
            entry = rp.record_suspense(
                db, tenant_id=tenant_id, reason=ln.reason or "bank_unmatched", amount=ln.amount,
                ref=ln.reference, suggested_loan_id=ln.match.suggested_loan_id, source=source,
                bill_ref=(ln.account or ln.description)[:60] or None, paid_at=ln.paid_at,
                payer_name=ln.payer_name, phone=ln.payer_phone,
                raw_payload={"file": file.filename, "line": ln.line, "bank_account": label,
                             "description": ln.description[:300]})
            db.flush()
            to_suspense += 1
            outcome.append({"line": ln.line, "result": "suspense", "suspense_id": entry.id})

    s = bs.summary(lines)
    write_audit(db, tenant_id=tenant_id, user=user, action="payments.bank_import",
                entity_type="bank_statement", entity_id=(file.filename or "")[:40],
                details={"bank_account": label, "applied": applied, "pending_approval": parked,
                         "suspense": to_suspense, "left_out": left_out,
                         "duplicates": s["duplicate"]["count"], "errors": s["error"]["count"]},
                request=request)
    db.commit()
    return {"filename": file.filename, "applied": applied, "pending_approval": parked,
            "suspense": to_suspense, "left_out": left_out,
            "duplicates": s["duplicate"]["count"], "errors": s["error"]["count"],
            "skipped": s["skipped"]["count"], "lines": outcome}
