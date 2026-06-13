# ============================================================
# BizGuard — Configuration Settings
# FILE: config.py
# Loads all environment variables with validation.
# ============================================================

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_env: str = "development"
    app_version: str = "1.0.0"
    debug: bool = True

    # Supabase
    supabase_url: str
    supabase_service_key: str
    database_url: str

    # Gemini
    gemini_api_key: str

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """Cached settings — loaded once, reused everywhere."""
    return Settings()