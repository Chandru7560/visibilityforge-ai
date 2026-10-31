import os
import secrets

from dotenv import load_dotenv

load_dotenv()


class BaseConfig:
    SECRET_KEY = os.environ.get("SECRET_KEY")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    OWNER_EMAIL = os.environ.get("OWNER_EMAIL", "owner@example.com")
    INITIAL_ADMIN_EMAIL = os.environ.get(
        "INITIAL_ADMIN_EMAIL", os.environ.get("OWNER_EMAIL", "admin@example.com")
    )
    INITIAL_ADMIN_PASSWORD = os.environ.get("INITIAL_ADMIN_PASSWORD")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    MAX_CONTENT_LENGTH = 2_100_000
    AUDIT_MAX_PAGES = int(os.environ.get("AUDIT_MAX_PAGES", "25"))
    AUDIT_MAX_DEPTH = int(os.environ.get("AUDIT_MAX_DEPTH", "2"))
    AUDIT_REQUEST_INTERVAL = float(os.environ.get("AUDIT_REQUEST_INTERVAL", "1.0"))
    AUDIT_REQUEST_TIMEOUT = float(os.environ.get("AUDIT_REQUEST_TIMEOUT", "3.0"))
    AUDIT_MAX_DURATION = float(os.environ.get("AUDIT_MAX_DURATION", "20.0"))
    AUDIT_MAX_RESPONSE_BYTES = int(
        os.environ.get("AUDIT_MAX_RESPONSE_BYTES", "512000")
    )
    CONTENT_LLM_API_KEY = os.environ.get("CONTENT_LLM_API_KEY")
    CONTENT_LLM_API_URL = os.environ.get(
        "CONTENT_LLM_API_URL", "https://api.openai.com/v1/chat/completions"
    )
    CONTENT_LLM_MODEL = os.environ.get("CONTENT_LLM_MODEL", "gpt-4o-mini")
    CONTENT_LLM_TIMEOUT = float(os.environ.get("CONTENT_LLM_TIMEOUT", "15"))
    LOGIN_RATE_LIMIT_ATTEMPTS = int(os.environ.get("LOGIN_RATE_LIMIT_ATTEMPTS", "5"))
    LOGIN_RATE_LIMIT_WINDOW_SECONDS = int(
        os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")
    )
    LOGIN_RATE_LIMIT_IP_ATTEMPTS = int(
        os.environ.get("LOGIN_RATE_LIMIT_IP_ATTEMPTS", "30")
    )


class DevelopmentConfig(BaseConfig):
    APP_ENV = "development"
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or (
        "sqlite:///visibilityforge-dev.db"
    )
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(BaseConfig):
    APP_ENV = "production"
    SECRET_KEY = os.environ.get("SECRET_KEY")
    DATABASE_URL = os.environ.get("DATABASE_URL")
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(BaseConfig):
    APP_ENV = "testing"
    SECRET_KEY = secrets.token_bytes(32)
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SESSION_COOKIE_SECURE = False
