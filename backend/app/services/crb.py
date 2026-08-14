"""
Credit Reference Bureau (CRB) integration — provider-abstracted & credential-gated.

`CrbProvider` is the interface; Metropol is the default implementation, with
TransUnion and Creditinfo stubs. Selection is by CRB_PROVIDER env var. Every
provider is CREDENTIAL-GATED: with no real credentials the check returns
status="not_configured" and a clear "credentials required" message — it NEVER
fabricates a score.

Env:
  CRB_PROVIDER   metropol | transunion | creditinfo
  CRB_BASE_URL   bureau API base
  CRB_API_KEY    (Metropol uses api key + username/password/hash)
  CRB_USERNAME / CRB_PASSWORD
"""
from __future__ import annotations

from datetime import datetime

import httpx

from app.core.config import settings


class CrbNotConfigured(RuntimeError):
    """Raised when the selected bureau has no real credentials."""


class CrbProvider:
    name = "base"

    def configured(self) -> bool:
        raise NotImplementedError

    def check(self, *, national_id: str, first_name: str, last_name: str,
              phone: str | None = None) -> dict:
        raise NotImplementedError


def _looks_real(*vals: str) -> bool:
    for v in vals:
        v = (v or "").strip()
        if not v or v.lower() == "placeholder":
            return False
    return True


class MetropolCrbProvider(CrbProvider):
    """Metropol CRB (Kenya) — 'Crystobol'/Metropol API.

    Real call (annotated): Metropol authenticates each request with an API key +
    a per-request SHA256 hash of (api_key + rest_id + timestamp). The identity
    verification / credit-score product is:

        POST {CRB_BASE_URL}/identity/verify   (or /score)
        headers: {"apikey": CRB_API_KEY, "hash": <sha256>, "rest_id": <username>}
        body: {"identity_type": "001", "identity_number": <national_id>, ...}
    """
    name = "metropol"

    def configured(self) -> bool:
        return _looks_real(settings.CRB_API_KEY, settings.CRB_USERNAME)

    def check(self, *, national_id, first_name, last_name, phone=None) -> dict:
        if not self.configured():
            raise CrbNotConfigured("Metropol CRB credentials required "
                                   "(set CRB_API_KEY and CRB_USERNAME/CRB_PASSWORD).")
        import hashlib
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        digest = hashlib.sha256(
            f"{settings.CRB_API_KEY}{settings.CRB_USERNAME}{ts}".encode()).hexdigest()
        payload = {"identity_number": national_id, "identity_type": "001",
                   "first_name": first_name, "last_name": last_name, "phone": phone}
        with httpx.Client(timeout=25) as client:
            resp = client.post(
                f"{settings.CRB_BASE_URL.rstrip('/')}/identity/verify",
                headers={"apikey": settings.CRB_API_KEY, "hash": digest,
                         "rest_id": settings.CRB_USERNAME},
                json=payload,
            )
            resp.raise_for_status()
            body = resp.json()
        return _normalise(self.name, body)


class TransUnionCrbProvider(CrbProvider):
    """TransUnion Kenya — stub. Real integration:
        POST {CRB_BASE_URL}/creditreport with OAuth/basic (CRB_USERNAME/PASSWORD).
    """
    name = "transunion"

    def configured(self) -> bool:
        return _looks_real(settings.CRB_USERNAME, settings.CRB_PASSWORD)

    def check(self, *, national_id, first_name, last_name, phone=None) -> dict:
        if not self.configured():
            raise CrbNotConfigured("TransUnion CRB credentials required "
                                   "(set CRB_USERNAME and CRB_PASSWORD).")
        payload = {"nationalId": national_id, "firstName": first_name, "lastName": last_name}
        with httpx.Client(timeout=25) as client:
            resp = client.post(
                f"{settings.CRB_BASE_URL.rstrip('/')}/creditreport",
                auth=(settings.CRB_USERNAME, settings.CRB_PASSWORD), json=payload)
            resp.raise_for_status()
            body = resp.json()
        return _normalise(self.name, body)


class CreditinfoCrbProvider(CrbProvider):
    """Creditinfo CRB — stub. Real integration:
        POST {CRB_BASE_URL}/credit-report with basic auth (CRB_USERNAME/PASSWORD).
    """
    name = "creditinfo"

    def configured(self) -> bool:
        return _looks_real(settings.CRB_USERNAME, settings.CRB_PASSWORD)

    def check(self, *, national_id, first_name, last_name, phone=None) -> dict:
        if not self.configured():
            raise CrbNotConfigured("Creditinfo CRB credentials required "
                                   "(set CRB_USERNAME and CRB_PASSWORD).")
        payload = {"nationalId": national_id, "firstName": first_name, "lastName": last_name}
        with httpx.Client(timeout=25) as client:
            resp = client.post(
                f"{settings.CRB_BASE_URL.rstrip('/')}/credit-report",
                auth=(settings.CRB_USERNAME, settings.CRB_PASSWORD), json=payload)
            resp.raise_for_status()
            body = resp.json()
        return _normalise(self.name, body)


PROVIDERS: dict[str, type[CrbProvider]] = {
    "metropol": MetropolCrbProvider,
    "transunion": TransUnionCrbProvider,
    "creditinfo": CreditinfoCrbProvider,
}


def get_provider() -> CrbProvider:
    return PROVIDERS.get((settings.CRB_PROVIDER or "metropol").lower(), MetropolCrbProvider)()


def _normalise(provider: str, body: dict) -> dict:
    """Map a bureau-specific payload to our common shape. Real field names vary by
    bureau — adjust the getters below per provider once live credentials arrive."""
    def g(*keys, default=None):
        for k in keys:
            if isinstance(body, dict) and body.get(k) is not None:
                return body[k]
        return default
    return {
        "provider": provider,
        "status": "ok",
        "reference": g("reference", "reportId", "requestId"),
        "credit_score": g("score", "creditScore", "delinquencyScore"),
        "active_accounts": g("activeAccounts", "openAccounts"),
        "defaults_count": g("defaults", "adverseAccounts", "npaAccounts"),
        "total_outstanding": g("totalOutstanding", "outstandingBalance"),
        "raw": body,
    }


def integration_status() -> str:
    """LIVE when the selected provider has real credentials, else NOT CONFIGURED."""
    return "LIVE" if get_provider().configured() else "NOT CONFIGURED"


def run_check(*, national_id: str, first_name: str, last_name: str,
              phone: str | None = None) -> dict:
    """Run a CRB check with the configured provider. Returns the normalised dict,
    or a not_configured result (never fabricated data)."""
    provider = get_provider()
    if not provider.configured():
        return {"provider": provider.name, "status": "not_configured",
                "reference": None, "credit_score": None, "active_accounts": None,
                "defaults_count": None, "total_outstanding": None,
                "error": f"{provider.name.title()} CRB credentials required.",
                "raw": {}}
    try:
        return provider.check(national_id=national_id, first_name=first_name,
                              last_name=last_name, phone=phone)
    except CrbNotConfigured as exc:
        return {"provider": provider.name, "status": "not_configured",
                "reference": None, "credit_score": None, "active_accounts": None,
                "defaults_count": None, "total_outstanding": None,
                "error": str(exc), "raw": {}}
    except Exception as exc:
        return {"provider": provider.name, "status": "error", "reference": None,
                "credit_score": None, "active_accounts": None, "defaults_count": None,
                "total_outstanding": None, "error": str(exc), "raw": {}}
