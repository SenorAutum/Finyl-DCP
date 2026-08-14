"""
Finyl-DCP — Application configuration.

All runtime configuration is sourced from environment variables (see .env.example
at the repo root). This keeps the codebase 100% portable: the same image runs on
this VM, docker-compose, AWS, GCP, etc. — only the env differs.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- Database ---------------------------------------------------------
    DATABASE_URL: str = "postgresql://finyl:finyl@localhost:5432/finyl_dcp"
    DB_SCHEMA: str = "finyl_dcp"  # dedicated schema; set "public" for a dedicated DB

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
    LLM_VISION_MODEL: str = ""   # blank -> use LLM_MODEL for vision OCR too

    # --- Safaricom Daraja placeholders (swap in real credentials here) ------
    # Credential-gated: while key/secret are placeholders the payment endpoints
    # report NOT CONFIGURED and refuse to fake a success. Add real sandbox creds
    # + restart and the same code flips to LIVE (SANDBOX) with zero changes.
    DARAJA_ENV: str = "sandbox"                    # sandbox | production
    DARAJA_CONSUMER_KEY: str = "placeholder"
    DARAJA_CONSUMER_SECRET: str = "placeholder"
    DARAJA_SHORTCODE: str = "placeholder"
    DARAJA_PASSKEY: str = "placeholder"
    DARAJA_INITIATOR_NAME: str = "finyl-api"
    DARAJA_SECURITY_CREDENTIAL: str = "placeholder"   # encrypted initiator password (B2C)
    DARAJA_CALLBACK_BASE_URL: str = "https://finyl-dcp.abacusai.cloud"

    # --- Uwazii Mobile bulk-SMS gateway (LIVE) -------------------------------
    # Real credentials are injected into the environment from the secret store;
    # never hardcoded/committed. When the access token is present the SMS service
    # dispatches live messages via the Uwazii REST API.
    UWAZII_BASE_URL: str = "https://restapi.uwaziimobile.com/v1/send"
    UWAZII_ACCESS_TOKEN: str = ""
    UWAZII_SENDER_ID: str = ""

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


settings = Settings()
