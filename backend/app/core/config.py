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

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
