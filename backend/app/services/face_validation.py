"""Provider-agnostic face-match + liveness client (Phase 2).

Initial implementation is a deterministic mock (stable score per client) with a
pluggable real-provider hook (`_provider_call`). Default provider label is
`smile_identity`, matching the migration default; swap `_provider_call` for the
real Smile Identity / other KYC-provider integration when creds are wired.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import FaceValidationLog, TenantValidationPrefs

PASS_THRESHOLD = Decimal("0.85")
REVIEW_THRESHOLD = Decimal("0.60")


def _provider_call(*, provider: str, id_image_path, selfie_image_path, client_id) -> dict:
    """Mock provider call — deterministic score derived from client id + paths.

    Replace with a real HTTP call to the configured provider. Must return
    {match_score: float 0..1, liveness_pass: bool, job_id: str, raw: dict}.
    """
    seed = f"{client_id}:{id_image_path}:{selfie_image_path}".encode()
    digest = hashlib.sha256(seed).hexdigest()
    score = 0.55 + (int(digest[:4], 16) % 4500) / 10000.0  # 0.55 .. ~0.99
    liveness = int(digest[4:6], 16) % 10 != 0               # ~90% pass
    return {"match_score": round(score, 4), "liveness_pass": liveness,
            "job_id": f"mock-{digest[:16]}", "raw": {"mock": True, "provider": provider}}


def validate(db: Session, *, tenant_id: int, client_id: int, id_image_path=None,
             selfie_image_path=None, provider: str | None = None,
             validated_by: int | None = None) -> FaceValidationLog:
    provider = provider or "smile_identity"
    res = _provider_call(provider=provider, id_image_path=id_image_path,
                         selfie_image_path=selfie_image_path, client_id=client_id)
    score = Decimal(str(res["match_score"]))
    if score >= PASS_THRESHOLD and res["liveness_pass"]:
        result = "pass"
    elif score >= REVIEW_THRESHOLD:
        result = "manual_review"
    else:
        result = "fail"
    log = FaceValidationLog(
        tenant_id=tenant_id, client_id=client_id, validation_provider=provider,
        match_score=score, result=result, liveness_pass=res["liveness_pass"],
        id_image_path=id_image_path, selfie_image_path=selfie_image_path,
        smile_job_id=res["job_id"], raw_response=res["raw"],
        validated_at=datetime.now(timezone.utc), validated_by=validated_by,
    )
    db.add(log)
    db.commit()
    return log


def latest_status(db: Session, *, tenant_id: int, client_id: int) -> dict:
    log = (db.query(FaceValidationLog)
           .filter(FaceValidationLog.tenant_id == tenant_id,
                   FaceValidationLog.client_id == client_id)
           .order_by(FaceValidationLog.validated_at.desc()).first())
    prefs = (db.query(TenantValidationPrefs)
             .filter(TenantValidationPrefs.tenant_id == tenant_id).first())
    mandatory = bool(prefs.face_validation_mandatory) if prefs else False
    if not log:
        return {"client_id": client_id, "result": "not_run", "mandatory": mandatory}
    return {"client_id": client_id, "result": log.result,
            "match_score": float(log.match_score) if log.match_score is not None else None,
            "liveness_pass": log.liveness_pass, "provider": log.validation_provider,
            "validated_at": log.validated_at, "mandatory": mandatory}
