"""
eKYC identity verification (MOCK).

>>> PLACEHOLDER — REAL eKYC PROVIDER INTEGRATION GOES HERE <<<
The request/response shapes below mirror a Creditinfo IDM style decision API:

    POST {EKYC_BASE_URL}/decision
    Auth: basic {EKYC_USERNAME}:{EKYC_PASSWORD}
    body: {"strategyId": EKYC_STRATEGY_ID,
           "data": {"nationalId": ..., "firstName": ..., "lastName": ...,
                    "dateOfBirth": "YYYY-MM-DD", "phoneNumber": ...}}

    200: {"reference": "...", "status": "VERIFIED"|"NOT_VERIFIED",
          "matchScore": 0-100, "verifiedName": "...", "checks": {...}}

Set EKYC_MOCK=false and fill EKYC_* env vars to call the live provider — the
`verify_identity()` return shape stays identical so callers never change.
"""
import hashlib
import uuid
from datetime import datetime

import httpx

from app.core.config import settings

# Which government/registry checks the provider reports back on.
CHECK_KEYS = ["idRegistryMatch", "nameMatch", "dateOfBirthMatch", "deceasedRegister", "sanctionsList"]


class EkycNotConfigured(RuntimeError):
    """Raised when eKYC has no real credentials and mock mode is off."""


def is_configured() -> bool:
    """True when real Creditinfo IDM credentials are present."""
    for v in (settings.EKYC_USERNAME, settings.EKYC_PASSWORD, settings.EKYC_STRATEGY_ID):
        if not v or str(v).strip().lower() == "placeholder":
            return False
    return True


def integration_status() -> str:
    if settings.EKYC_MOCK:
        return "SANDBOX"
    return "LIVE" if is_configured() else "NOT CONFIGURED"


def _deterministic_score(national_id: str, full_name: str) -> int:
    """Stable pseudo-score so the same client always yields the same result
    (demo data stays consistent across reloads)."""
    digest = hashlib.sha256(f"{national_id}|{full_name}".encode()).hexdigest()
    return 55 + int(digest[:4], 16) % 46          # 55-100


def verify_identity(*, national_id: str, first_name: str, last_name: str,
                    middle_name: str | None = None, date_of_birth=None,
                    phone: str | None = None) -> dict:
    """Run an identity check. Returns the provider-shaped response dict."""
    full_name = " ".join(p for p in [first_name, middle_name, last_name] if p)
    payload = {
        "strategyId": settings.EKYC_STRATEGY_ID,
        "data": {
            "nationalId": national_id,
            "firstName": first_name,
            "middleName": middle_name,
            "lastName": last_name,
            "dateOfBirth": str(date_of_birth) if date_of_birth else None,
            "phoneNumber": phone,
        },
    }

    if not settings.EKYC_MOCK:
        # ---- LIVE CALL — credential-gated -----------------------------------
        # No mock-pass: without real credentials we refuse rather than fake a
        # verification. The caller surfaces this as a clear 4xx.
        if not is_configured():
            raise EkycNotConfigured(
                "eKYC credentials required — set EKYC_USERNAME, EKYC_PASSWORD and "
                "EKYC_STRATEGY_ID (or enable EKYC_MOCK for local demos).")
        with httpx.Client(timeout=20) as client:
            resp = client.post(
                f"{settings.EKYC_BASE_URL.rstrip('/')}/decision",
                json=payload,
                auth=(settings.EKYC_USERNAME, settings.EKYC_PASSWORD),
            )
            resp.raise_for_status()
            body = resp.json()
        body.setdefault("request", payload)
        return body

    # ---- MOCK ---------------------------------------------------------------
    score = _deterministic_score(national_id or "", full_name)
    verified = score >= 70
    return {
        "provider": "creditinfo-idm (mock)",
        "reference": f"IDM-{uuid.uuid4().hex[:10].upper()}",
        "status": "VERIFIED" if verified else "NOT_VERIFIED",
        "matchScore": score,
        "verifiedName": full_name.upper() if verified else None,
        "checks": {
            "idRegistryMatch": verified,
            "nameMatch": score >= 75,
            "dateOfBirthMatch": bool(date_of_birth) and score >= 65,
            "deceasedRegister": "clear",
            "sanctionsList": "clear",
        },
        "checkedAt": datetime.utcnow().isoformat() + "Z",
        "request": payload,
    }
