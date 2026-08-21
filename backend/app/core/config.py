"""
Finyl-DCP — Application configuration.

All runtime configuration is sourced from environment variables (see .env.example
at the repo root). This keeps the codebase 100% portable: the same image runs on
this VM, docker-compose, AWS, GCP, etc. — only the env differs.
"""
import re

from pydantic_settings import BaseSettings
from pydantic import model_validator

# AUTH-01 — a JWT secret that is the shipped placeholder or too short lets an
# attacker forge tokens (including role=super_admin). The app must refuse to run.
WEAK_JWT_SECRETS = {"change-me-in-production", "", "changeme", "secret", "your-secret-key"}
MIN_JWT_SECRET_LEN = 32

# INPUT-03 — DB_SCHEMA is interpolated into `SET search_path` via f-strings in
# core/database.py. It is config-derived (not user input), but we still validate
# it against a strict identifier pattern at startup as defence-in-depth so a
# malformed/injected value can never reach a SQL statement.
_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Settings(BaseSettings):
    # --- Database ---------------------------------------------------------
    DATABASE_URL: str = "postgresql://finyl:finyl@localhost:5432/finyl_dcp"
    DB_SCHEMA: str = "finyl_dcp"  # dedicated schema; set "public" for a dedicated DB
    # API-04 — the DB schema is owned by migrations/*.sql. `Base.metadata.create_all`
    # at startup causes silent schema drift, so it is OFF by default. Set true only
    # for a throwaway local/dev DB with no migrations applied.
    AUTO_CREATE_TABLES: bool = False

    # In-process auto-reconcile worker (APScheduler in the FastAPI process). When
    # enabled, a job every SCHEDULER_INTERVAL_MINUTES resolves stuck B2C payouts
    # for each payments-enabled tenant. Set false to disable (e.g. multi-replica).
    SCHEDULER_ENABLED: bool = True
    SCHEDULER_INTERVAL_MINUTES: int = 5
    SCHEDULER_STUCK_MINUTES: int = 10

    # PII-01 — optional explicit key (urlsafe-b64 Fernet key) for field-level PII
    # encryption at rest. Blank -> a stable key is derived from JWT_SECRET. Never logged.
    PII_ENCRYPTION_KEY: str = ""

    # --- Auth --------------------------------------------------------------
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_MINUTES: int = 60 * 12

    # --- LLM (OpenAI-compatible chat completions endpoint) ------------------
    # Also powers the vision-LLM National-ID OCR provider. gpt-5.5-mini is a cheap,
    # vision-capable default; gemini-3.5-flash is a good fallback. Configurable.
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = "sk-placeholder"
    LLM_MODEL: str = "gpt-5.5-mini"
    LLM_VISION_MODEL: str = "gpt-4o"   # MUST be a vision-capable model (LLM_MODEL may be text-only)

    # --- Safaricom Daraja placeholders (swap in real credentials here) ------
    # Credential-gated: while key/secret are placeholders the payment endpoints
    # report NOT CONFIGURED and refuse to fake a success. Add real sandbox creds
    # + restart and the same code flips to LIVE (SANDBOX) with zero changes.
    DARAJA_ENVIRONMENT: str = "sandbox"            # sandbox | production
    DARAJA_CONSUMER_KEY: str = "placeholder"
    DARAJA_CONSUMER_SECRET: str = "placeholder"
    DARAJA_SHORTCODE: str = "placeholder"
    DARAJA_PASSKEY: str = "placeholder"
    DARAJA_INITIATOR_NAME: str = "finyl-api"
    DARAJA_SECURITY_CREDENTIAL: str = "placeholder"   # encrypted initiator password (B2C)
    DARAJA_CALLBACK_BASE_URL: str = "https://finyl-dcp.abacusai.cloud"
    # Hard-to-guess path segment embedded in every Daraja callback URL we
    # register (B2C result/timeout, STK callback, C2B confirmation). Safaricom
    # sends NO auth header on these webhooks, so the unguessable path token is
    # the primary source-authentication control (defence-in-depth: pair with the
    # nginx Safaricom IP allow-list — see deploy/finyl-dcp.conf). OVERRIDE THIS
    # PER-ENVIRONMENT via the MPESA_CALLBACK_TOKEN env var; the default below is
    # only a working placeholder for the credential-gated mock/demo. It is NOT a
    # cryptographic secret and is never logged.
    MPESA_CALLBACK_TOKEN: str = "finyl-daraja-hook-3f9c2a"

    # --- Uwazii Mobile bulk-SMS gateway (LIVE) -------------------------------
    # Real credentials are injected into the environment from the secret store;
    # never hardcoded/committed. When the access token is present the SMS service
    # dispatches live messages via the Uwazii REST API.
    UWAZII_BASE_URL: str = "https://restapi.uwaziimobile.com/v1/send"
    UWAZII_AUTH_URL: str = "https://restapi.uwaziimobile.com/v1/authorize"
    UWAZII_TOKEN_URL: str = "https://restapi.uwaziimobile.com/v1/accesstoken"
    UWAZII_USERNAME: str = ""
    UWAZII_PASSWORD: str = ""
    UWAZII_ACCESS_TOKEN: str = ""  # optional static override; blank -> use two-step auth
    UWAZII_SENDER_ID: str = ""
    # API-05 — optional shared-secret token for the unauthenticated Uwazii DLR
    # (delivery-report) webhook. When set, the DLR callback URL must carry this
    # token (path segment /sms/dlr/{token} or X-DLR-Token header) or it is 404'd;
    # the plain tokenless /sms/dlr routes are rejected. Blank -> legacy open
    # behaviour (delivery status is advisory only). Never logged.
    UWAZII_DLR_TOKEN: str = ""

    # --- Bulk SMS provider placeholders (legacy generic gateway) --------------
    SMS_API_URL: str = "placeholder"
    SMS_API_KEY: str = "placeholder"

    # --- CRB (Credit Reference Bureau) — provider-abstracted -----------------
    # Credential-gated. metropol (default) | transunion | creditinfo.
    CRB_PROVIDER: str = "metropol"
    CRB_BASE_URL: str = "https://api.metropol.co.ke/v2"
    CRB_API_KEY: str = ""
    CRB_USERNAME: str = ""
    CRB_PASSWORD: str = ""

    # --- Client document storage ---------------------------------------------
    # Local filesystem by default; point at a mounted volume in Docker/K8s or
    # swap app/services/storage.py for S3/GCS without touching the routers.
    STORAGE_DIR: str = "storage"
    MAX_UPLOAD_MB: int = 10

    # --- OCR (National ID "Process ID" action) --------------------------------
    # Local Tesseract by default — no cloud dependency, no per-page cost.
    # vision_llm = hybrid vision-LLM primary + Tesseract fallback (recommended).
    OCR_PROVIDER: str = "vision_llm"         # vision_llm | tesseract
    TESSERACT_CMD: str = "tesseract"         # absolute path if not on PATH
    OCR_LANGUAGES: str = "eng"

    # --- eKYC identity-verification provider placeholders ----------------------
    # Shapes mirror Creditinfo IDM (username/password + strategy id). Swap in real
    # credentials and flip EKYC_MOCK=false to hit the live provider.
    # Credential-gated: while username/password are placeholders the verify
    # endpoint reports NOT CONFIGURED and returns a clear "credentials required"
    # error instead of faking a pass. EKYC_MOCK is retained for local demos only.
    EKYC_BASE_URL: str = "https://idmtest.creditinfo.co.ke"
    EKYC_USERNAME: str = ""
    EKYC_PASSWORD: str = ""
    EKYC_STRATEGY_ID: str = ""
    EKYC_MOCK: bool = False

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def DARAJA_BASE_URL(self) -> str:
        """Daraja API base URL derived from DARAJA_ENVIRONMENT.

        production -> https://api.safaricom.co.ke
        sandbox    -> https://sandbox.safaricom.co.ke  (default / any non-prod value)
        """
        env = (self.DARAJA_ENVIRONMENT or "sandbox").strip().lower()
        if env.startswith("prod"):
            return "https://api.safaricom.co.ke"
        return "https://sandbox.safaricom.co.ke"

    @model_validator(mode="after")
    def _enforce_strong_jwt_secret(self):
        """AUTH-01 boot guard: refuse to start with a weak/placeholder JWT secret.

        The error message never echoes the secret value — only its length and the
        policy that was violated.
        """
        secret = (self.JWT_SECRET or "").strip()
        if secret in WEAK_JWT_SECRETS or secret.lower() in WEAK_JWT_SECRETS:
            raise ValueError(
                "Refusing to start: JWT_SECRET is the default placeholder or a "
                "known-weak value. Set a strong random JWT_SECRET (>= "
                f"{MIN_JWT_SECRET_LEN} chars) in the environment. "
                "Generate one with: python -c \"import secrets;print(secrets.token_urlsafe(48))\""
            )
        if len(secret) < MIN_JWT_SECRET_LEN:
            raise ValueError(
                "Refusing to start: JWT_SECRET is too short "
                f"(got {len(secret)} chars, need >= {MIN_JWT_SECRET_LEN}). "
                "Generate a strong one with: "
                "python -c \"import secrets;print(secrets.token_urlsafe(48))\""
            )
        # INPUT-03: DB_SCHEMA is interpolated into `SET search_path`; enforce a
        # strict identifier pattern so a malformed value can never reach SQL.
        schema = (self.DB_SCHEMA or "").strip()
        if not _SCHEMA_RE.match(schema):
            raise ValueError(
                "Refusing to start: DB_SCHEMA is not a valid SQL identifier "
                "(must match ^[A-Za-z_][A-Za-z0-9_]*$)."
            )
        return self


settings = Settings()
