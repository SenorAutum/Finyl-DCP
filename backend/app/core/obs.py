"""
OPS-01 — structured application logging (detective control).

A single place that configures stdout/journald logging and exposes helpers for
the three security-relevant event classes the audit calls out:

  * auth outcomes      — failed logins, lockouts, revoked tokens
  * permission denials — module/permission/role gate rejections
  * money movement     — disbursements, refunds, reconciliations, callback postings

Events are emitted as ``key=value`` structured lines under dedicated logger names
(``finyl.security`` / ``finyl.money``) so they are easy to grep and route. PII is
minimised: we log user id/role/email and the action, never passwords, tokens or
full request bodies. Callers pass only non-secret identifiers.
"""
import logging
import os
import sys

_CONFIGURED = False

security_log = logging.getLogger("finyl.security")
money_log = logging.getLogger("finyl.money")


def configure_logging() -> None:
    """Idempotently configure root logging to stdout (captured by journald under
    systemd). Level from LOG_LEVEL env (default INFO)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    ))
    root = logging.getLogger()
    # Avoid duplicate handlers if uvicorn already installed one.
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def _kv(**fields) -> str:
    parts = []
    for k, v in fields.items():
        if v is None:
            continue
        s = str(v).replace("\n", " ").replace(" ", "_") if isinstance(v, str) else v
        parts.append(f"{k}={s}")
    return " ".join(parts)


def log_auth_event(event: str, *, email=None, user_id=None, ip=None, detail=None,
                   ok: bool = False) -> None:
    """Auth outcome (login success/failure, lockout, revocation)."""
    msg = _kv(evt="auth", event=event, ok=ok, user_id=user_id, email=email,
              ip=ip, detail=detail)
    (security_log.info if ok else security_log.warning)(msg)


def log_permission_denied(*, kind: str, needed, user_id=None, role=None, ip=None,
                          path=None) -> None:
    """A module/permission/role gate rejected the request."""
    security_log.warning(_kv(evt="authz_denied", kind=kind, needed=needed,
                             user_id=user_id, role=role, ip=ip, path=path))


def log_money_event(action: str, *, tenant_id=None, user_id=None, loan_id=None,
                    amount=None, phone=None, ref=None, detail=None) -> None:
    """Money-movement event (disburse/refund/reconcile/callback posting)."""
    money_log.info(_kv(evt="money", action=action, tenant_id=tenant_id,
                       user_id=user_id, loan_id=loan_id, amount=amount,
                       phone=phone, ref=ref, detail=detail))


# ---------------------------------------------------------------------------
# Phase 2 — immutable audit trail (CBK Digital Credit Providers mandate)
# ---------------------------------------------------------------------------
#
# The DB enforces immutability at rest: migration 021 installs ON UPDATE/ON
# DELETE ``DO INSTEAD NOTHING`` rules on ``audit_logs`` so rows can only ever be
# INSERTed — no ORM or raw path can mutate or hard-delete a posted entry. This
# module complements that with an insert-only application contract: audit rows
# are written via ``deps.write_audit`` (INSERT) and NEVER loaded-then-mutated.
# Do not add an update/delete code path for AuditLog; treat it as append-only.
#
# CBK-relevant structured event *types* — a stable, greppable vocabulary that
# regulators and SOC tooling can key off. Use these as the ``action`` value on
# audit rows and on the structured security log so the two correlate.

# Auth & access
CBK_LOGIN = "cbk.auth.login"
CBK_LOGIN_FAILED = "cbk.auth.login_failed"
CBK_OTP_ISSUED = "cbk.auth.otp_issued"
CBK_OTP_VERIFIED = "cbk.auth.otp_verified"
CBK_DEVICE_BOUND = "cbk.auth.device_bound"
CBK_DEVICE_REVOKED = "cbk.auth.device_revoked"
CBK_GEOFENCE_BLOCK = "cbk.access.geofence_block"
CBK_TIMEFENCE_BLOCK = "cbk.access.timefence_block"
# KYC & onboarding
CBK_KYC_DECISION = "cbk.kyc.decision"
CBK_KYC_ESCALATION = "cbk.kyc.escalation"
CBK_FACE_VALIDATION = "cbk.kyc.face_validation"
CBK_AGE_REJECTED = "cbk.kyc.age_rejected"
CBK_CLIENT_CONVERTED = "cbk.kyc.client_converted"
# Client data governance
CBK_CLIENT_EDIT_REQUEST = "cbk.client.edit_request"
CBK_CLIENT_EDIT_APPROVED = "cbk.client.edit_approved"
CBK_CLIENT_EDIT_REJECTED = "cbk.client.edit_rejected"
CBK_WALLET_LOCKED = "cbk.client.wallet_locked"
CBK_EDIT_LOCK_ENFORCED = "cbk.client.edit_lock_enforced"
# Lending
CBK_LOAN_ACTIVE_LOCK = "cbk.loan.active_lock"
CBK_DISBURSEMENT = "cbk.loan.disbursement"
# Collections
CBK_PTP_CREATED = "cbk.collections.ptp_created"
CBK_RATIBA_CONSENT = "cbk.collections.ratiba_consent"

# Set of every CBK event type, for validation/enumeration in tooling.
CBK_EVENT_TYPES = frozenset(
    v for k, v in list(globals().items())
    if k.startswith("CBK_") and isinstance(v, str)
)


def log_cbk_event(action: str, *, tenant_id=None, user_id=None, entity_type=None,
                  entity_id=None, outcome=None, detail=None) -> None:
    """Emit a structured CBK-mandate audit event to the security log.

    This is the *observability* half of the audit trail (greppable, routed to
    SOC/journald). The durable, immutable half is the ``audit_logs`` row written
    by ``deps.write_audit``. Callers that persist an audit row for a regulated
    action should also call this so the log stream and the DB stay correlated.
    Pass one of the ``CBK_*`` constants as ``action``.
    """
    security_log.info(_kv(evt="cbk", action=action, tenant_id=tenant_id,
                          user_id=user_id, entity_type=entity_type,
                          entity_id=entity_id, outcome=outcome, detail=detail))
