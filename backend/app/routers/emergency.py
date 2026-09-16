"""Emergency admin endpoints — REMOVE in production.

Provides unauthenticated password reset for initial setup/recovery.
DELETE THIS FILE after successful deployment.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.models import User

router = APIRouter(prefix="/api/emergency", tags=["emergency"])


@router.post("/reset-password")
def emergency_reset_password(
    email: str,
    new_password: str,
    db: Session = Depends(get_db)
):
    """Emergency unauthenticated password reset. REMOVE AFTER USE.
    
    Usage: POST /api/emergency/reset-password?email=superadmin@finyl.app&new_password=FINYL@2026
    """
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(404, f"User {email} not found")
    
    user.hashed_password = hash_password(new_password)
    user.force_password_reset = False
    db.commit()
    
    return {
        "ok": True,
        "email": user.email,
        "message": f"Password reset successfully. You can now log in with {email}"
    }
