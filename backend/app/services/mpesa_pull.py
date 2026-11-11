"""WP12 — catching paybill and till payments whose notification never arrived.

Safaricom tells Finyl about each paybill / till payment by calling it (C2B
confirmation). If Finyl is down or the network drops that call, the payment
used to stay unrecorded until somebody noticed. Daraja's "Pull Transactions"
service lets Finyl ask for the payments of the last 48 hours instead.

  * Once per DCP number (paybill and each till) Finyl registers for the service,
    quoting the number nominated on the M-Pesa organisation account.
  * Every hour Finyl asks for the last few hours. Each payment that is not yet
    recorded goes through exactly the same routine as a live notification, so
    it is matched, applied or parked in suspense the same way, and a payment
    that is already recorded is never counted twice.

Safaricom only allows this on live (production) numbers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import TenantIntegrationConfig

logger = logging.getLogger("finyl.payments")
PULL_INTEGRATION = "mpesa_pull"
NBO = ZoneInfo("Africa/Nairobi")
DEFAULT_HOURS = 3
MAX_HOURS = 48
MAX_PAGES = 50


def get_state(db, tenant_id: int) -> dict:
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == PULL_INTEGRATION).first())
    return dict((row.config or {}) if row else {})


def save_state(db, tenant_id: int, state: dict, user_id: int | None = None) -> None:
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == PULL_INTEGRATION).first())
    if row is None:
        db.add(TenantIntegrationConfig(tenant_id=tenant_id, integration=PULL_INTEGRATION,
                                       config=state, secrets={}, enabled=True,
                                       updated_by_user_id=user_id))
    else:
        row.config = state
        if user_id:
            row.updated_by_user_id = user_id
    db.flush()


def dcp_numbers(db, tenant_id: int) -> list[str]:
    """The DCP's paybill first, then its tills."""
    from app.services import webhook_security as ws
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == "daraja").first())
    cfg = (row.config or {}) if row else {}
    out = [str(cfg.get("shortcode")).strip()] if cfg.get("shortcode") else []
    return out + [t for t in ws.till_numbers(cfg) if t not in out]


def register(db, tenant_id: int, nominated: str, user_id: int | None = None) -> list[dict]:
    """Register every DCP number for Pull Transactions."""
    from app.services import mpesa
    msisdn = mpesa.normalise_msisdn(nominated or "")
    if len(msisdn) != 12:
        raise ValueError("Enter the number nominated on the M-Pesa organisation account "
                         "(shown in the M-Pesa business portal under the shortcode's details).")
    creds = mpesa.resolve_creds(db, tenant_id)
    if not creds.configured:
        raise ValueError("This DCP has no M-Pesa settings yet (or they are switched off).")
    numbers = dcp_numbers(db, tenant_id)
    if not numbers:
        raise ValueError("This DCP has no paybill or till number in its M-Pesa settings.")
    state = get_state(db, tenant_id)
    state["nominated_number"] = msisdn
    regs = dict(state.get("numbers") or {})
    results = []
    for number in numbers:
        try:
            resp = mpesa.register_pull(creds, number, msisdn)
            code = str(resp.get("ResponseStatus") or resp.get("ResponseCode") or "")
            ok = code in ("1000", "1001", "0")             # 1001 = already registered
            detail = resp.get("ResponseDescription") or str(resp)[:200]
        except Exception as exc:
            ok = False
            detail = (getattr(getattr(exc, "response", None), "text", "") or str(exc))[:300]
        if ok:
            regs[number] = {"registered_at": datetime.utcnow().isoformat() + "Z"}
        results.append({"number": number, "ok": ok, "detail": detail})
    state["numbers"] = regs
    save_state(db, tenant_id, state, user_id)
    return results


def _transactions(obj) -> list[dict]:
    """Every transaction in Safaricom's answer, wherever it is nested."""
    found = []
    if isinstance(obj, dict):
        if "transactionId" in obj:
            return [obj]
        for v in obj.values():
            found.extend(_transactions(v))
    elif isinstance(obj, list):
        for v in obj:
            found.extend(_transactions(v))
    elif isinstance(obj, str) and obj.strip().startswith(("[", "{")):
        import json
        try:
            found.extend(_transactions(json.loads(obj)))
        except ValueError:
            pass
    return found


def _trans_time(value) -> str:
    """'2026-11-05T10:13:00Z' -> '20261105101300' (Kenyan time, as C2B sends it).
    Safaricom writes these times in Kenyan time even when they end in Z."""
    s = str(value or "").strip().replace("Z", "").replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s[:19], fmt).strftime("%Y%m%d%H%M%S")
        except ValueError:
            continue
    return ""


def as_c2b(tx: dict, number: str) -> dict:
    """A pulled transaction in the shape of a C2B confirmation."""
    msisdn = str(tx.get("msisdn") or "")
    return {"TransactionType": str(tx.get("transactiontype") or "Pull"),
            "TransID": str(tx.get("transactionId") or "").strip(),
            "TransTime": _trans_time(tx.get("trxDate")),
            "TransAmount": str(tx.get("amount") or "0"),
            "BusinessShortCode": number,
            "BillRefNumber": str(tx.get("billreference") or "").strip(),
            "MSISDN": msisdn, "FirstName": "", "Source": "pull"}


def pull_for_tenant(db, tenant_id: int, hours: int = DEFAULT_HOURS, now=None) -> dict:
    """Ask Safaricom for the last `hours` of payments to every registered DCP
    number and record those that are missing."""
    from app.routers import payments
    from app.services import mpesa, repayments as rp
    from app.services import webhook_security as ws
    hours = max(1, min(int(hours or DEFAULT_HOURS), MAX_HOURS))
    state = get_state(db, tenant_id)
    numbers = [n for n in dcp_numbers(db, tenant_id) if n in (state.get("numbers") or {})]
    out = {"numbers": numbers, "found": 0, "recovered": 0, "errors": []}
    if not numbers:
        return out
    creds = mpesa.resolve_creds(db, tenant_id)
    if not creds.configured:
        out["errors"].append("M-Pesa settings missing or switched off")
        return out
    end = (now or datetime.now(NBO)).astimezone(NBO)
    start = end - timedelta(hours=hours)
    for number in numbers:
        for page in range(MAX_PAGES):
            try:
                resp = mpesa.query_pull(creds, number, start.strftime("%Y-%m-%d %H:%M:%S"),
                                        end.strftime("%Y-%m-%d %H:%M:%S"), page)
            except Exception as exc:
                out["errors"].append(f"{number}: {str(exc)[:200]}")
                break
            txs = [t for t in _transactions(resp) if str(t.get("transactionId") or "").strip()]
            if not txs:
                break
            out["found"] += len(txs)
            for tx in txs:
                body = as_c2b(tx, number)
                if rp.reference_already_used(db, tenant_id, body["TransID"]):
                    continue
                payments._ingest_and_process(
                    db, ws.ENDPOINT_MPESA_PULL, body, payments._process_c2b_callback,
                    {"ResultCode": 0, "ResultDesc": "Recorded from Pull Transactions"})
                if rp.reference_already_used(db, tenant_id, body["TransID"]):
                    out["recovered"] += 1
                    logger.warning("mpesa_pull_recovered tenant_id=%s shortcode=%s ref=%s",
                                   tenant_id, number, body["TransID"])
    state = get_state(db, tenant_id)
    state["last_run"] = {"at": datetime.utcnow().isoformat() + "Z", "hours": hours,
                         "found": out["found"], "recovered": out["recovered"],
                         "errors": out["errors"][:5]}
    save_state(db, tenant_id, state)
    db.commit()
    return out


def run_pull_all(db, hours: int = DEFAULT_HOURS) -> dict:
    """Hourly job body: every active DCP that registered numbers."""
    from app.models import Tenant
    total = {"tenants": 0, "recovered": 0}
    rows = (db.query(TenantIntegrationConfig)
            .filter(TenantIntegrationConfig.integration == PULL_INTEGRATION).all())
    for row in rows:
        tenant = db.get(Tenant, row.tenant_id)
        if tenant is None or tenant.active is False or not (row.config or {}).get("numbers"):
            continue
        try:
            res = pull_for_tenant(db, row.tenant_id, hours)
            total["tenants"] += 1
            total["recovered"] += res["recovered"]
        except Exception:
            db.rollback()
            logger.exception("mpesa_pull failed for tenant %s", row.tenant_id)
    return total
