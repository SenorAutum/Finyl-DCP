"""Face validation trigger + status (Phase 2)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_module, require_permission, write_audit
from app.models import Borrower
from app.schemas import FaceValidateIn
from app.services import face_validation as svc

router = APIRouter(prefix="/api/v1/clients", tags=["face-validation"])


@router.post("/{client_id}/face-validate")
def face_validate(client_id: int, body: FaceValidateIn | None = None,
                  tenant_id: int = Depends(require_module("clients")),
                  user=Depends(require_permission("clients.create", "clients.edit", mode="any")),
                  db: Session = Depends(get_db), request: Request = None):
    body = body or FaceValidateIn()
    if not db.query(Borrower).filter(Borrower.id == client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    log = svc.validate(db, tenant_id=tenant_id, client_id=client_id,
                       id_image_path=body.id_image_path,
                       selfie_image_path=body.selfie_image_path,
                       provider=body.provider, validated_by=user.id)
    write_audit(db, tenant_id=tenant_id, user=user, action="client.face_validate",
                entity_type="client", entity_id=client_id,
                details={"result": log.result}, request=request)
    db.commit()
    return {"client_id": client_id, "result": log.result,
            "match_score": float(log.match_score) if log.match_score is not None else None,
            "liveness_pass": log.liveness_pass, "provider": log.validation_provider,
            "validated_at": log.validated_at}


@router.get("/{client_id}/face-validation-status")
def face_status(client_id: int, tenant_id: int = Depends(require_module("clients")),
                db: Session = Depends(get_db)):
    if not db.query(Borrower).filter(Borrower.id == client_id,
                                     Borrower.tenant_id == tenant_id).first():
        raise HTTPException(404, "Client not found")
    return svc.latest_status(db, tenant_id=tenant_id, client_id=client_id)
