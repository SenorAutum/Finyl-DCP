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
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_API_KEY: str = "sk-placeholder"
    LLM_MODEL: str = "gpt-4o-mini"

    # --- Safaricom Daraja placeholders (swap in real credentials here) ------
    DARAJA_CONSUMER_KEY: str = "placeholder"
    DARAJA_CONSUMER_SECRET: str = "placeholder"
    DARAJA_SHORTCODE: str = "placeholder"
    DARAJA_PASSKEY: str = "placeholder"

    # --- Bulk SMS provider placeholders --------------------------------------
    SMS_API_URL: str = "placeholder"
    SMS_API_KEY: str = "placeholder"

    # --- Client document storage ---------------------------------------------
    # Local filesystem by default; point at a mounted volume in Docker/K8s or
    # swap app/services/storage.py for S3/GCS without touching the routers.
    STORAGE_DIR: str = "storage"
    MAX_UPLOAD_MB: int = 10

    # --- OCR (National ID "Process ID" action) --------------------------------
    # Local Tesseract by default — no cloud dependency, no per-page cost.
    OCR_PROVIDER: str = "tesseract"          # tesseract | (add your vendor here)
    TESSERACT_CMD: str = "tesseract"         # absolute path if not on PATH
    OCR_LANGUAGES: str = "eng"

    # --- eKYC identity-verification provider placeholders ----------------------
    # Shapes mirror Creditinfo IDM (username/password + strategy id). Swap in real
    # credentials and flip EKYC_MOCK=false to hit the live provider.
    EKYC_BASE_URL: str = "https://api.creditinfo-idm.example/v1"
    EKYC_USERNAME: str = "placeholder"
    EKYC_PASSWORD: str = "placeholder"
    EKYC_STRATEGY_ID: str = "placeholder"
    EKYC_MOCK: bool = True

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
