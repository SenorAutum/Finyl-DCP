"""
WP12 — instant payment notifications from banks.

Most Kenyan banks can call a web address the moment money lands in an account
(an "IPN" or "payment notification"). Each bank names its fields differently,
so a DCP sets up each of its banks here:

  * Finyl gives the DCP a private address for that bank
    (.../api/v1/payments/hooks/bank/<key>) to hand to the bank;
  * the bank proves each message is genuine with a shared secret: either a
    signature over the message (HMAC-SHA256) or the secret itself in a header;
    the bank's own internet addresses can also be required;
  * the DCP says which field holds the reference, amount, date, account typed
    by the payer, narrative, payer name and number (dotted paths such as
    "transaction.amount", or "items.0.amount" inside a list).

A genuine notification is handled like a paybill payment: matched on the
account the payer typed (loan account number or customer number) or found in
the narrative, applied at once; anything that does not match goes to the
Suspense Account (source "bank_ipn"). A reference already recorded is never
counted twice. Each notification is stored first and retried if processing
fails, like M-Pesa callbacks.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import ipaddress
import re
import secrets as pysecrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app.core.config import settings
from app.core.crypto import decrypt_pii, encrypt_pii
from app.core.money import money
from app.models import TenantIntegrationConfig

INTEGRATION = "bank_ipn"
ENDPOINT = "bank-ipn"
AUTH_MODES = ("hmac_sha256", "shared_key")
MAP_FIELDS = ("reference", "amount", "date", "account", "narrative", "payer_name",
              "payer_phone", "direction")
DEBIT_WORDS = {"dr", "debit", "d", "out", "withdrawal", "outgoing"}


class BankConfigError(ValueError):
    """Shown to the DCP administrator."""


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _row(db, tenant_id):
    return (db.query(TenantIntegrationConfig)
            .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                    TenantIntegrationConfig.integration == INTEGRATION).first())


def public_url(key: str) -> str:
    base = (settings.DARAJA_CALLBACK_BASE_URL or "").rstrip("/")
    return f"{base}/api/v1/payments/hooks/bank/{key}"


def list_banks(db, tenant_id: int) -> list[dict]:
    row = _row(db, tenant_id)
    cfg, sec = ((row.config or {}), (row.secrets or {})) if row else ({}, {})
    out = []
    for b in cfg.get("banks") or []:
        out.append({**{k: v for k, v in b.items() if k != "key"},
                    "url": public_url(b["key"]),
                    "secret_set": bool(sec.get(b["id"]))})
    return out


def _clean_bank(body: dict, existing: dict | None) -> dict:
    name = " ".join(str(body.get("name") or "").split())[:60]
    if not name:
        raise BankConfigError("Give the bank a name, e.g. Equity collections account.")
    auth = str(body.get("auth_mode") or (existing or {}).get("auth_mode") or "hmac_sha256")
    if auth not in AUTH_MODES:
        raise BankConfigError(f"auth_mode must be one of {AUTH_MODES}")
    header = " ".join(str(body.get("auth_header") or "").split())[:60] or (
        "X-Signature" if auth == "hmac_sha256" else "X-Api-Key")
    fmap = {}
    for f in MAP_FIELDS:
        v = str((body.get("field_map") or {}).get(f) or "").strip()[:120]
        if v:
            fmap[f] = v
    for need in ("reference", "amount"):
        if need not in fmap:
            raise BankConfigError(f"Say which field holds the {need}.")
    if "account" not in fmap and "narrative" not in fmap:
        raise BankConfigError("Say which field holds the account the payer typed, or the narrative.")
    ips = []
    for ip in str(body.get("allowed_ips") or "").replace(",", " ").split():
        try:
            ipaddress.ip_network(ip, strict=False)
        except ValueError:
            raise BankConfigError(f"{ip} is not an internet address.")
        ips.append(ip)
    return {"name": name, "auth_mode": auth, "auth_header": header, "field_map": fmap,
            "amount_in_cents": bool(body.get("amount_in_cents")),
            "allowed_ips": ips, "active": bool(body.get("active", True))}


def save_bank(db, tenant_id: int, user_id: int, body: dict, bank_id: str | None = None) -> dict:
    row = _row(db, tenant_id)
    # Deep copies: the stored settings must change as a whole to be saved.
    cfg = copy.deepcopy((row.config or {}) if row else {})
    sec = dict((row.secrets or {}) if row else {})
    banks = cfg.get("banks") or []
    existing = next((b for b in banks if b["id"] == bank_id), None) if bank_id else None
    if bank_id and existing is None:
        raise BankConfigError("No such bank set-up.")
    clean = _clean_bank(body, existing)
    if existing is None:
        if len(banks) >= 20:
            raise BankConfigError("At most 20 bank set-ups per DCP.")
        existing = {"id": pysecrets.token_hex(4), "key": pysecrets.token_urlsafe(24)}
        banks.append(existing)
    existing.update(clean)
    secret = str(body.get("secret") or "").strip()
    if secret:
        if len(secret) < 16:
            raise BankConfigError("The shared secret must be at least 16 characters.")
        sec[existing["id"]] = encrypt_pii(secret)
    if not sec.get(existing["id"]):
        raise BankConfigError("Enter the shared secret agreed with the bank.")
    cfg["banks"] = banks
    if row is None:
        db.add(TenantIntegrationConfig(tenant_id=tenant_id, integration=INTEGRATION, config=cfg,
                                       secrets=sec, enabled=True, updated_by_user_id=user_id))
    else:
        row.config, row.secrets, row.updated_by_user_id = cfg, sec, user_id
    db.flush()
    return next(b for b in list_banks(db, tenant_id) if b["id"] == existing["id"])


def delete_bank(db, tenant_id: int, user_id: int, bank_id: str) -> None:
    row = _row(db, tenant_id)
    if row is None:
        return
    cfg, sec = copy.deepcopy(row.config or {}), dict(row.secrets or {})
    cfg["banks"] = [b for b in cfg.get("banks") or [] if b["id"] != bank_id]
    sec.pop(bank_id, None)
    row.config, row.secrets, row.updated_by_user_id = cfg, sec, user_id
    db.flush()


def find_by_key(db, key: str):
    """(tenant_id, bank, secret) for a notification address, or None."""
    for row in (db.query(TenantIntegrationConfig)
                .filter(TenantIntegrationConfig.integration == INTEGRATION).all()):
        for b in (row.config or {}).get("banks") or []:
            if hmac.compare_digest(str(b.get("key") or ""), str(key or "")):
                return row.tenant_id, b, decrypt_pii((row.secrets or {}).get(b["id"]))
    return None


# --------------------------------------------------------------------------- #
# Is it genuine?
# --------------------------------------------------------------------------- #
def verify(bank: dict, secret: str | None, raw: bytes, headers, ip: str | None) -> str | None:
    """None when genuine, else why not."""
    if not bank.get("active", True):
        return "this bank set-up is switched off"
    if bank.get("allowed_ips"):
        try:
            addr = ipaddress.ip_address(ip or "")
        except ValueError:
            return "unknown sender address"
        if not any(addr in ipaddress.ip_network(n, strict=False) for n in bank["allowed_ips"]):
            return f"sender address {ip} is not allowed"
    if not secret:
        return "no shared secret set"
    given = str(headers.get(bank.get("auth_header") or "") or "").strip()
    if not given:
        return f"missing {bank.get('auth_header')} header"
    if bank.get("auth_mode") == "shared_key":
        return None if hmac.compare_digest(given, secret) else "wrong key"
    digest = hmac.new(secret.encode(), raw or b"", hashlib.sha256).digest()
    candidates = {digest.hex(), base64.b64encode(digest).decode()}
    given = given.split("=", 1)[1] if given.lower().startswith("sha256=") else given
    return None if any(hmac.compare_digest(given.lower() if len(given) == 64 else given, c)
                       for c in candidates) else "signature does not match"


# --------------------------------------------------------------------------- #
# Reading a notification
# --------------------------------------------------------------------------- #
def get_path(obj, path: str):
    """'transaction.amount' / 'items.0.amount' inside a notification; keys are
    matched exactly first, then ignoring capitals."""
    cur = obj
    for part in str(path or "").split("."):
        if part == "":
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
            else:
                cur = next((v for k, v in cur.items() if str(k).lower() == part.lower()), None)
        else:
            return None
        if cur is None:
            return None
    return cur


def _when(value) -> datetime | None:
    """ISO times (with or without zone) or bank formats -> naive UTC."""
    from app.services.bank_import import parse_date
    if value in (None, ""):
        return None
    s = str(value).strip()
    if re.fullmatch(r"\d{14}", s):                     # 20261105143000 (Kenyan time)
        return datetime.strptime(s, "%Y%m%d%H%M%S") - timedelta(hours=3)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is not None:
            return d.astimezone(timezone.utc).replace(tzinfo=None)
        return d - timedelta(hours=3)                  # no zone: Kenyan time
    except ValueError:
        return parse_date(s)


def read(bank: dict, payload: dict) -> dict:
    """The mapped fields of one notification, checked."""
    fmap = bank.get("field_map") or {}
    val = {f: get_path(payload, p) for f, p in fmap.items()}
    out = {"reference": None, "amount": None, "paid_at": None, "account": "",
           "narrative": "", "payer_name": None, "payer_phone": None, "problem": None}
    from app.services.payment_approvals import clean_reference
    out["reference"] = clean_reference(val.get("reference"))
    try:
        amt = Decimal(str(val.get("amount")).replace(",", "").strip()) if val.get("amount") not in (None, "") else None
    except InvalidOperation:
        amt = None
    if amt is not None and bank.get("amount_in_cents"):
        amt = amt / 100
    out["amount"] = money(amt) if amt is not None else None
    try:
        out["paid_at"] = _when(val.get("date")) or datetime.utcnow()
    except ValueError:
        out["paid_at"] = datetime.utcnow()
    out["account"] = str(val.get("account") or "").strip()[:60]
    out["narrative"] = " ".join(str(val.get("narrative") or "").split())[:300]
    out["payer_name"] = (str(val.get("payer_name")).strip()[:120] or None) if val.get("payer_name") else None
    out["payer_phone"] = (str(val.get("payer_phone")).strip()[:20] or None) if val.get("payer_phone") else None
    direction = str(val.get("direction") or "").strip().lower()
    if not out["reference"]:
        out["problem"] = "no reference"
    elif out["amount"] is None:
        out["problem"] = "no amount"
    elif out["amount"] <= 0 or direction in DEBIT_WORDS:
        out["problem"] = "not money in"
    return out


def match(db, tenant_id: int, fields: dict):
    """(loan or None, how, reason, suggested_loan_id)."""
    from app.services.bank_import import Line, _match_line
    ln = Line(line=0, status="review", description=fields["narrative"], account=fields["account"],
              payer_name=fields["payer_name"], payer_phone=fields["payer_phone"])
    m = _match_line(db, tenant_id, ln, "")
    return m.loan, m.how, m.reason, m.suggested_loan_id


def process(db, tenant_id: int, bank: dict, payload: dict) -> dict:
    """Record one genuine notification. Idempotent on the reference."""
    from app.services import repayments as rp
    from app.models import Loan
    f = read(bank, payload)
    if f["problem"]:
        return {"status": "ignored", "reason": f["problem"]}
    if rp.reference_already_used(db, tenant_id, f["reference"]):
        return {"status": "duplicate", "reference": f["reference"]}
    loan, how, reason, suggested = match(db, tenant_id, f)
    if loan is None:
        entry = rp.record_suspense(
            db, tenant_id=tenant_id, reason=reason or "bank_unmatched", amount=f["amount"],
            ref=f["reference"], phone=f["payer_phone"], suggested_loan_id=suggested,
            source="bank_ipn", bill_ref=(f["account"] or f["narrative"])[:60] or None,
            payer_name=f["payer_name"], paid_at=f["paid_at"],
            raw_payload={"bank": bank.get("name"), "narrative": f["narrative"]})
        db.flush()
        return {"status": "suspense", "suspense_id": entry.id, "reference": f["reference"]}
    loan = db.query(Loan).filter(Loan.id == loan.id, Loan.tenant_id == tenant_id).with_for_update().first()
    res = rp.apply_payment(db, tenant_id=tenant_id, loan=loan, amount=f["amount"], ref=f["reference"],
                           method="bank_notice", paid_at=f["paid_at"], phone=f["payer_phone"],
                           source="bank_ipn", bill_ref=(f["account"] or None),
                           payer_name=f["payer_name"])
    return {"status": res.status, "loan_id": loan.id, "matched_by": how,
            "applied": float(res.applied), "reference": f["reference"]}


def dry_run(db, tenant_id: int, bank: dict, payload: dict) -> dict:
    """What a sample notification would do, without recording anything."""
    from app.services import repayments as rp
    f = read(bank, payload)
    out = {"reference": f["reference"], "amount": float(f["amount"]) if f["amount"] is not None else None,
           "paid_at": f["paid_at"].isoformat() + "Z" if f["paid_at"] else None,
           "account": f["account"], "narrative": f["narrative"], "payer_name": f["payer_name"],
           "payer_phone": f["payer_phone"], "problem": f["problem"]}
    if f["problem"]:
        out["result"] = f"ignored: {f['problem']}"
        return out
    if rp.reference_already_used(db, tenant_id, f["reference"]):
        out["result"] = "already recorded"
        return out
    loan, how, reason, suggested = match(db, tenant_id, f)
    out["result"] = (f"applied to loan {loan.account_number} (by {how.replace('_', ' ')})" if loan
                     else f"Suspense Account: {reason}" + (f", suggested loan #{suggested}" if suggested else ""))
    return out
