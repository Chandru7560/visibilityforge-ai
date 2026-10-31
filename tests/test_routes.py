from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import _normalize_database_url, create_app
from app.routes import health as health_module
from app.config import ProductionConfig, TestingConfig


def test_home_page_returns_html(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.mimetype == "text/html"
    assert b"VisibilityForge AI" in response.data
    assert b"Everywhere people search." in response.data


def test_health_endpoint_returns_small_json_response(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_health_endpoint_reports_database_failure(client, monkeypatch, caplog):
    def fail_query(_statement):
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(
        health_module,
        "db",
        SimpleNamespace(session=SimpleNamespace(execute=fail_query)),
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}
    assert "Database health check failed." in caplog.text


def test_unknown_page_returns_friendly_not_found_page(client):
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert b"That page isn't in view." in response.data


def test_production_requires_postgresql_database():
    class InvalidProductionConfig(ProductionConfig):
        SECRET_KEY = TestingConfig.SECRET_KEY
        DATABASE_URL = "sqlite:///:memory:"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"

    with pytest.raises(RuntimeError, match="Production requires a PostgreSQL"):
        create_app(InvalidProductionConfig)


@pytest.mark.parametrize(
    ("database_url", "expected_url"),
    [
        ("postgres://db.example/visibilityforge", "postgresql+psycopg://db.example/visibilityforge"),
        ("postgresql://db.example/visibilityforge", "postgresql+psycopg://db.example/visibilityforge"),
        ("postgresql+psycopg://db.example/visibilityforge", "postgresql+psycopg://db.example/visibilityforge"),
    ],
)
def test_postgresql_urls_use_psycopg_driver(database_url, expected_url):
    assert _normalize_database_url(database_url) == expected_url


def test_production_uses_postgresql_and_secure_session_cookies(monkeypatch):
    class ValidProductionConfig(ProductionConfig):
        SECRET_KEY = TestingConfig.SECRET_KEY
        DATABASE_URL = "postgres://db.example/visibilityforge"
        SQLALCHEMY_DATABASE_URI = DATABASE_URL

    app = create_app(ValidProductionConfig)
    monkeypatch.setattr(
        health_module,
        "db",
        SimpleNamespace(session=SimpleNamespace(execute=lambda _statement: None)),
    )

    assert app.config["SQLALCHEMY_DATABASE_URI"] == (
        "postgresql+psycopg://db.example/visibilityforge"
    )
    assert app.config["SESSION_COOKIE_SECURE"] is True
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    response = app.test_client().get("/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_testing_configuration_keeps_isolated_sqlite_database():
    class TestingConfigWithDatabaseURL(TestingConfig):
        DATABASE_URL = "postgresql+psycopg://example.invalid/test"

    app = create_app(TestingConfigWithDatabaseURL)

    assert app.config["SQLALCHEMY_DATABASE_URI"] == "sqlite:///:memory:"
