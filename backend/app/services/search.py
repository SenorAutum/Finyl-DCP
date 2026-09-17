"""Global multi-entity search (Phase 2).

Searches across clients, loans, leads, staff and products, tenant-scoped and
narrowed to the caller's data-scope. National ID / full-name are encrypted at
rest, so DB-side search uses the indexed plaintext columns (names, phone,
account number) via ILIKE; a phone query also matches the borrower blind-index.
Also resolves keyword → navigation-route commands (e.g. "new loan").
"""
from __future__ import annotations

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.crypto import pii_hash
from app.models import Borrower, Loan, Staff, CrmLead, Product

# keyword → frontend route (navigation commands)
_NAV_COMMANDS = {
    "new client": "/clients/new", "add client": "/clients/new",
    "new loan": "/loans/new", "add loan": "/loans/new",
    "leads": "/crm", "pipeline": "/crm",
    "dashboard": "/dashboard", "reports": "/reports",
    "settings": "/settings", "users": "/settings/users",
    "collections": "/collections", "ptp": "/collections/ptp",
    "field": "/field-ops", "gps": "/field-ops/gps",
    "guarantors": "/guarantors", "audit": "/audit",
}


def _nav_hits(q: str) -> list[dict]:
    ql = q.lower().strip()
    out = []
    for kw, route in _NAV_COMMANDS.items():
        if ql in kw or kw in ql:
            out.append({"entity_type": "command", "id": 0, "label": kw.title(),
                        "sublabel": "Go to", "route": route, "rank": 1.0})
    return out[:3]


def search(db: Session, *, tenant_id: int, scope, q: str, limit: int = 20) -> dict:
    q = (q or "").strip()
    if len(q) < 2:
        return {"query": q, "total": 0, "hits": []}
    like = f"%{q}%"
    hits: list[dict] = []
    hits.extend(_nav_hits(q))

    # --- Clients -----------------------------------------------------------
    cq = db.query(Borrower).filter(Borrower.tenant_id == tenant_id)
    cq = scope.apply_client(cq, Borrower)
    conds = [Borrower.first_name.ilike(like), Borrower.last_name.ilike(like),
             Borrower.phone.ilike(like)]
    h = pii_hash(q)
    if h:
        conds.append(Borrower.national_id_hash == h)
    for c in cq.filter(or_(*conds)).limit(limit).all():
        hits.append({"entity_type": "client", "id": c.id, "label": c.full_name,
                     "sublabel": c.phone, "route": f"/clients/{c.id}", "rank": 0.9})

    # --- Loans -------------------------------------------------------------
    lq = db.query(Loan).filter(Loan.tenant_id == tenant_id)
    lq = scope.apply_loan(lq, Loan)
    for ln in lq.filter(Loan.account_number.ilike(like)).limit(limit).all():
        hits.append({"entity_type": "loan", "id": ln.id, "label": ln.account_number,
                     "sublabel": f"status={ln.status}", "route": f"/loans/{ln.id}", "rank": 0.85})

    # --- Leads -------------------------------------------------------------
    for ld in (db.query(CrmLead)
               .filter(CrmLead.tenant_id == tenant_id,
                       or_(CrmLead.name.ilike(like), CrmLead.phone.ilike(like)))
               .limit(limit).all()):
        hits.append({"entity_type": "lead", "id": ld.id, "label": ld.name,
                     "sublabel": ld.phone, "route": f"/crm/{ld.id}", "rank": 0.7})

    # --- Staff (company-scope roles only) ----------------------------------
    if getattr(scope, "company_wide", False):
        for st in (db.query(Staff)
                   .filter(Staff.tenant_id == tenant_id,
                           or_(Staff.name.ilike(like), Staff.phone.ilike(like)))
                   .limit(limit).all()):
            hits.append({"entity_type": "staff", "id": st.id, "label": st.name,
                         "sublabel": st.phone, "route": f"/settings/staff/{st.id}",
                         "rank": 0.6})

    # --- Products ----------------------------------------------------------
    for pr in (db.query(Product)
               .filter(Product.tenant_id == tenant_id, Product.name.ilike(like))
               .limit(limit).all()):
        hits.append({"entity_type": "product", "id": pr.id, "label": pr.name,
                     "sublabel": "product", "route": f"/settings/products/{pr.id}",
                     "rank": 0.5})

    hits.sort(key=lambda h: h.get("rank", 0), reverse=True)
    hits = hits[:limit]
    return {"query": q, "total": len(hits), "hits": hits}
