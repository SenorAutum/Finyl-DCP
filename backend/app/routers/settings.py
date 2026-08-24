"""
Per-DCP configuration surface — reachable by the DCP's OWN administrator
(``system_admin``), scoped to the caller's own tenant via ``get_tenant_id``.
super_admin passes the role gate too (and may target another tenant via the
X-Tenant-Id header), but this is the DCP self-service console, NOT the platform
registry (that lives in routers/admin.py + routers/integrations.py).

Everything here is own-tenant and server-side validated. Secrets (M-Pesa/Daraja
credentials) are ENCRYPTED AT REST with Fernet (app.core.crypto.encrypt_pii) and
NEVER returned in plaintext — responses expose a boolean "configured" plus the
last-4 characters only.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_tenant_id, require_permission, require_role, write_audit
from app.core.crypto import encrypt_pii, decrypt_pii
from app.core.permissions import (APPROVAL_TYPES, APPROVAL_TYPE_LABELS, ROLE_LABELS,
                                  eligible_approver_roles)
from app.models import (ApprovalThreshold, ApproverSetting, MODULE_KEYS,
                        SmsAutomationSetting, TenantIntegrationConfig, TenantModule, User,
                        EclProvisionConfig, ECL_DEFAULT_STAGE1_RATE,
                        ECL_DEFAULT_STAGE2_RATE, ECL_DEFAULT_STAGE3_RATE)
from app.schemas import (DarajaConfigIn, SmsAutomationIn, ThresholdCreate,
                        ApproverConfigIn, SettingsModuleToggle, EclConfigIn)
from app.services import mpesa, rbac
from app.routers.notifications import get_automation_config

# Whole router requires the DCP admin role (super_admin auto-passes require_role).
router = APIRouter(prefix="/api/v1/settings", tags=["settings"],
                   dependencies=[Depends(require_role("system_admin"))])

_SECRET_FIELDS = ("consumer_key", "consumer_secret", "passkey", "security_credential")
_VALID_ENVIRONMENTS = ("sandbox", "production")
_PLACEHOLDER = "placeholder"


def _mask(value: str | None) -> str | None:
    """Return a masked hint (last-4) for a secret, never the value itself."""
    if not value or value == _PLACEHOLDER:
        return None
    tail = value[-4:] if len(value) >= 4 else value
    return "\u2022\u2022\u2022\u2022" + tail


# ---------------------------------------------------------------------------
# M-Pesa / Daraja credentials (per DCP, encrypted at rest)
# ---------------------------------------------------------------------------
@router.get("/daraja")
def get_daraja(tenant_id: int = Depends(get_tenant_id),
               db: Session = Depends(get_db),
               _: User = Depends(require_permission("thresholds.manage"))):
    """Return the tenant's own Daraja config — non-secret fields in the clear,
    secrets only as configured/last-4. ``integration_status`` reflects the
    EFFECTIVE credentials (own values with per-field fallback to platform .env)."""
    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == "daraja").first())
    cfg = (row.config or {}) if row else {}
    sec = (row.secrets or {}) if row else {}

    secrets_state = {}
    for f in _SECRET_FIELDS:
        raw = decrypt_pii(sec.get(f)) if sec.get(f) else None
        raw = None if raw == _PLACEHOLDER else raw
        secrets_state[f] = {"configured": bool(raw), "hint": _mask(raw)}

    creds = mpesa.resolve_creds(db, tenant_id)
    return {
        "enabled": bool(row.enabled) if row else False,
        "has_own_config": row is not None,
        "environment": cfg.get("environment"),
        "shortcode": cfg.get("shortcode"),
        "initiator_name": cfg.get("initiator_name"),
        "secrets": secrets_state,
        "integration_status": mpesa.integration_status(creds),
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


@router.put("/daraja")
def update_daraja(body: DarajaConfigIn, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  db: Session = Depends(get_db),
                  actor: User = Depends(require_permission("thresholds.manage"))):
    """Upsert the tenant's Daraja config. Non-secret fields (environment,
    shortcode, initiator_name) are stored in the clear; secret fields are
    encrypted with Fernet. Secrets sent blank/omitted keep the stored value."""
    if body.environment is not None and body.environment not in _VALID_ENVIRONMENTS:
        raise HTTPException(422, f"environment must be one of {_VALID_ENVIRONMENTS}")

    row = (db.query(TenantIntegrationConfig)
           .filter(TenantIntegrationConfig.tenant_id == tenant_id,
                   TenantIntegrationConfig.integration == "daraja").first())
    cfg = dict(row.config or {}) if row else {}
    sec = dict(row.secrets or {}) if row else {}

    # Non-secret fields — update only when provided (empty string clears).
    for field in ("environment", "shortcode", "initiator_name"):
        val = getattr(body, field)
        if val is not None:
            val = val.strip()
            cfg[field] = val or None

    # Secret fields — encrypt and store only when a non-blank value is supplied.
    changed_secrets = []
    for field in _SECRET_FIELDS:
        val = getattr(body, field)
        if val is not None and val.strip():
            sec[field] = encrypt_pii(val.strip())
            changed_secrets.append(field)

    enabled = row.enabled if row else True
    if body.enabled is not None:
        enabled = bool(body.enabled)

    if row:
        row.config = cfg
        row.secrets = sec
        row.enabled = enabled
        row.updated_by_user_id = actor.id
    else:
        row = TenantIntegrationConfig(tenant_id=tenant_id, integration="daraja",
                                      config=cfg, secrets=sec, enabled=enabled,
                                      updated_by_user_id=actor.id)
        db.add(row)
    # Audit records WHICH fields changed, never the secret values.
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.daraja.update",
                entity_type="integration_config",
                details={"non_secret": {k: cfg.get(k) for k in ("environment", "shortcode", "initiator_name")},
                         "secrets_updated": changed_secrets, "enabled": enabled},
                request=request)
    db.commit()
    return get_daraja(tenant_id=tenant_id, db=db, _=actor)


@router.post("/daraja/test")
def test_daraja(tenant_id: int = Depends(get_tenant_id),
                db: Session = Depends(get_db),
                _: User = Depends(require_permission("thresholds.manage"))):
    """Attempt a live OAuth handshake with the tenant's effective credentials."""
    return mpesa.test_connection(mpesa.resolve_creds(db, tenant_id))


# ---------------------------------------------------------------------------
# SMS automation (per DCP)
# ---------------------------------------------------------------------------
@router.get("/sms-automation")
def get_sms_automation(tenant_id: int = Depends(get_tenant_id),
                       db: Session = Depends(get_db),
                       _: User = Depends(require_permission("messaging.manage"))):
    return get_automation_config(db, tenant_id)


@router.put("/sms-automation")
def set_sms_automation(body: SmsAutomationIn, request: Request,
                       tenant_id: int = Depends(get_tenant_id),
                       db: Session = Depends(get_db),
                       actor: User = Depends(require_permission("messaging.manage"))):
    if not (0 <= int(body.send_hour) <= 23):
        raise HTTPException(422, "send_hour must be between 0 and 23")
    row = (db.query(SmsAutomationSetting)
           .filter(SmsAutomationSetting.tenant_id == tenant_id).first())
    if row:
        row.automation_enabled = bool(body.automation_enabled)
        row.send_hour = int(body.send_hour)
        row.updated_by_user_id = actor.id
    else:
        row = SmsAutomationSetting(tenant_id=tenant_id,
                                   automation_enabled=bool(body.automation_enabled),
                                   send_hour=int(body.send_hour),
                                   updated_by_user_id=actor.id)
        db.add(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.sms_automation.update",
                entity_type="sms_automation",
                details={"automation_enabled": bool(body.automation_enabled),
                         "send_hour": int(body.send_hour)}, request=request)
    db.commit()
    return get_automation_config(db, tenant_id)


# ---------------------------------------------------------------------------
# Approver tiers (per DCP, own tenant)
# ---------------------------------------------------------------------------
@router.get("/approver-config")
def get_approver_config(tenant_id: int = Depends(get_tenant_id),
                        db: Session = Depends(get_db),
                        _: User = Depends(require_permission("thresholds.manage"))):
    """Own-tenant view of every eligible approver role per approval type with its
    current effective enabled state. relationship_officer is intentionally never
    eligible (front-line originator)."""
    rows = db.query(ApproverSetting).filter(ApproverSetting.tenant_id == tenant_id).all()
    stored = {(r.approval_type, r.role): bool(r.enabled) for r in rows}
    out = {"tenant_id": tenant_id, "approval_types": []}
    for at in APPROVAL_TYPES:
        roles = []
        for role in eligible_approver_roles(at):
            key = (at, role)
            configured = key in stored
            enabled = stored[key] if configured else rbac._default_approver_enabled(at, role)
            roles.append({
                "role": role, "label": ROLE_LABELS.get(role, role),
                "enabled": enabled, "configured": configured,
                "default": rbac._default_approver_enabled(at, role),
            })
        out["approval_types"].append({
            "approval_type": at, "label": APPROVAL_TYPE_LABELS.get(at, at), "roles": roles,
        })
    return out


@router.post("/approver-config")
def set_approver_config(body: ApproverConfigIn, request: Request,
                        tenant_id: int = Depends(get_tenant_id),
                        db: Session = Depends(get_db),
                        actor: User = Depends(require_permission("thresholds.manage"))):
    """Upsert a single approver toggle for the caller's OWN tenant. The tenant_id
    in the body is ignored — the toggle is always pinned to the resolved tenant."""
    if body.approval_type not in APPROVAL_TYPES:
        raise HTTPException(400, f"approval_type must be one of {APPROVAL_TYPES}")
    if body.role not in eligible_approver_roles(body.approval_type):
        raise HTTPException(400,
                            f"Role '{body.role}' is not an eligible approver for '{body.approval_type}'")
    row = db.query(ApproverSetting).filter(
        ApproverSetting.tenant_id == tenant_id,
        ApproverSetting.approval_type == body.approval_type,
        ApproverSetting.role == body.role,
    ).first()
    if row:
        row.enabled = bool(body.enabled)
        row.updated_by_user_id = actor.id
    else:
        row = ApproverSetting(tenant_id=tenant_id, approval_type=body.approval_type,
                              role=body.role, enabled=bool(body.enabled),
                              updated_by_user_id=actor.id)
        db.add(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.approver.set",
                entity_type="approver_setting",
                details={"approval_type": body.approval_type, "role": body.role,
                         "enabled": bool(body.enabled)}, request=request)
    db.commit()
    return {"status": "updated", "approval_type": body.approval_type,
            "role": body.role, "enabled": bool(body.enabled)}


# ---------------------------------------------------------------------------
# Maker-checker thresholds (per DCP, own tenant)
# ---------------------------------------------------------------------------
@router.get("/thresholds")
def list_thresholds(tenant_id: int = Depends(get_tenant_id),
                    db: Session = Depends(get_db),
                    _: User = Depends(require_permission("thresholds.view"))):
    rows = (db.query(ApprovalThreshold)
            .filter(ApprovalThreshold.tenant_id == tenant_id)
            .order_by(ApprovalThreshold.id).all())
    return [{"id": t.id, "scope_type": t.scope_type, "scope_key": t.scope_key,
             "threshold_type": t.threshold_type, "amount": float(t.amount)} for t in rows]


@router.post("/thresholds")
def upsert_threshold(body: ThresholdCreate, request: Request,
                     tenant_id: int = Depends(get_tenant_id),
                     db: Session = Depends(get_db),
                     actor: User = Depends(require_permission("thresholds.manage"))):
    row = db.query(ApprovalThreshold).filter(
        ApprovalThreshold.tenant_id == tenant_id,
        ApprovalThreshold.scope_type == body.scope_type,
        ApprovalThreshold.scope_key == body.scope_key,
        ApprovalThreshold.threshold_type == body.threshold_type,
    ).first()
    if row:
        row.amount = body.amount
    else:
        row = ApprovalThreshold(tenant_id=tenant_id, scope_type=body.scope_type,
                                scope_key=body.scope_key, threshold_type=body.threshold_type,
                                amount=body.amount)
        db.add(row)
    db.flush()
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.threshold.set",
                entity_type="threshold", entity_id=row.id,
                details=body.model_dump(), request=request)
    db.commit()
    return {"id": row.id, "scope_type": row.scope_type, "scope_key": row.scope_key,
            "threshold_type": row.threshold_type, "amount": float(row.amount)}


@router.delete("/thresholds/{tid}")
def delete_threshold(tid: int, request: Request,
                     tenant_id: int = Depends(get_tenant_id),
                     db: Session = Depends(get_db),
                     actor: User = Depends(require_permission("thresholds.manage"))):
    row = (db.query(ApprovalThreshold)
           .filter(ApprovalThreshold.id == tid, ApprovalThreshold.tenant_id == tenant_id).first())
    if not row:
        raise HTTPException(404, "Threshold not found")
    db.delete(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.threshold.delete",
                entity_type="threshold", entity_id=tid, request=request)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Module enable/disable (per DCP, own tenant)
# ---------------------------------------------------------------------------
@router.get("/modules")
def list_modules(tenant_id: int = Depends(get_tenant_id),
                 db: Session = Depends(get_db),
                 _: User = Depends(require_permission("thresholds.manage"))):
    flags = {k: False for k in MODULE_KEYS}
    for m in db.query(TenantModule).filter(TenantModule.tenant_id == tenant_id):
        if m.module_key in flags:
            flags[m.module_key] = bool(m.enabled)
    return {"module_keys": list(MODULE_KEYS), "modules": flags}


@router.post("/modules/toggle")
def toggle_module(body: SettingsModuleToggle, request: Request,
                  tenant_id: int = Depends(get_tenant_id),
                  db: Session = Depends(get_db),
                  actor: User = Depends(require_permission("thresholds.manage"))):
    if body.module_key not in MODULE_KEYS:
        raise HTTPException(422, f"module_key must be one of {list(MODULE_KEYS)}")
    row = (db.query(TenantModule)
           .filter(TenantModule.tenant_id == tenant_id,
                   TenantModule.module_key == body.module_key).first())
    if row:
        row.enabled = bool(body.enabled)
    else:
        row = TenantModule(tenant_id=tenant_id, module_key=body.module_key,
                           enabled=bool(body.enabled))
        db.add(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.module.toggle",
                entity_type="module",
                details={"module_key": body.module_key, "enabled": bool(body.enabled)},
                request=request)
    db.commit()
    return {"module_key": body.module_key, "enabled": bool(body.enabled)}



# ---------------------------------------------------------------------------
# IFRS 9 ECL provisioning rate configuration (per tenant)
# ---------------------------------------------------------------------------
def _ecl_config_row(db: Session, tenant_id: int):
    return (db.query(EclProvisionConfig)
            .filter(EclProvisionConfig.tenant_id == tenant_id).first())


@router.get("/ecl-config")
def get_ecl_config(tenant_id: int = Depends(get_tenant_id),
                   db: Session = Depends(get_db),
                   _: User = Depends(require_permission("thresholds.manage"))):
    """Return the tenant's IFRS 9 ECL staging rates (defaults if unset)."""
    row = _ecl_config_row(db, tenant_id)
    if not row:
        return {
            "stage1_rate": float(ECL_DEFAULT_STAGE1_RATE),
            "stage2_rate": float(ECL_DEFAULT_STAGE2_RATE),
            "stage3_rate": float(ECL_DEFAULT_STAGE3_RATE),
            "is_default": True,
        }
    return {
        "stage1_rate": float(row.stage1_rate),
        "stage2_rate": float(row.stage2_rate),
        "stage3_rate": float(row.stage3_rate),
        "is_default": False,
    }


@router.put("/ecl-config")
def put_ecl_config(body: EclConfigIn, request: Request,
                   tenant_id: int = Depends(get_tenant_id),
                   db: Session = Depends(get_db),
                   actor: User = Depends(require_permission("thresholds.manage"))):
    """Upsert the tenant's IFRS 9 ECL staging rates (fractions, e.g. 0.01 = 1%)."""
    for name, val in (("stage1_rate", body.stage1_rate),
                      ("stage2_rate", body.stage2_rate),
                      ("stage3_rate", body.stage3_rate)):
        if val < 0 or val > 1:
            raise HTTPException(422, f"{name} must be between 0 and 1")
    row = _ecl_config_row(db, tenant_id)
    if row:
        row.stage1_rate = body.stage1_rate
        row.stage2_rate = body.stage2_rate
        row.stage3_rate = body.stage3_rate
    else:
        row = EclProvisionConfig(tenant_id=tenant_id,
                                 stage1_rate=body.stage1_rate,
                                 stage2_rate=body.stage2_rate,
                                 stage3_rate=body.stage3_rate)
        db.add(row)
    write_audit(db, tenant_id=tenant_id, user=actor, action="config.ecl.update",
                entity_type="ecl_provision_config",
                details=body.model_dump(), request=request)
    db.commit()
    return {"stage1_rate": float(row.stage1_rate),
            "stage2_rate": float(row.stage2_rate),
            "stage3_rate": float(row.stage3_rate),
            "is_default": False}
