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
