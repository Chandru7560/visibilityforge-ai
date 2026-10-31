import logging
import os
import secrets
from logging.config import dictConfig

import click
from flask import Flask, g, render_template
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import DevelopmentConfig, ProductionConfig, TestingConfig
from app.extensions import csrf, db, login_manager, migrate
from app.models import ActivityLog, User
from app.routes import (
    auth_bp,
    audits_bp,
    citations_bp,
    content_bp,
    dashboard_bp,
    health_bp,
    main_bp,
    research_bp,
    reports_bp,
)
from app.routes.reports import register_report_commands


def _configure_logging() -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)s %(name)s: %(message)s"
                }
            },
            "handlers": {
                "wsgi": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://flask.logging.wsgi_errors_stream",
                    "formatter": "default",
                }
            },
            "root": {"level": os.environ.get("LOG_LEVEL", "INFO"), "handlers": ["wsgi"]},
        }
    )


def _normalize_database_url(database_url: str) -> str:
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def create_app(config_object=None) -> Flask:
    _configure_logging()
    app = Flask(__name__, instance_relative_config=True)

    if config_object is None:
        environment = os.environ.get("APP_ENV", "development").lower()
        configs = {
            "development": DevelopmentConfig,
            "production": ProductionConfig,
            "testing": TestingConfig,
        }
        try:
            config_object = configs[environment]
        except KeyError as exc:
            raise ValueError(
                "APP_ENV must be one of: development, production, testing"
            ) from exc

    app.config.from_object(config_object)
    if any(
        app.config.get(name, 0) < 1
        for name in (
            "LOGIN_RATE_LIMIT_ATTEMPTS",
            "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
            "LOGIN_RATE_LIMIT_IP_ATTEMPTS",
        )
    ):
        raise RuntimeError("Login rate-limit configuration values must be positive.")
    if app.config.get("APP_ENV") == "production":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if app.config.get("DATABASE_URL") and app.config.get("APP_ENV") != "testing":
        app.config["SQLALCHEMY_DATABASE_URI"] = _normalize_database_url(
            app.config["DATABASE_URL"]
        )

    if app.config.get("APP_ENV") == "production":
        if not app.config.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be configured in production.")
        database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if not database_uri.startswith(("postgresql://", "postgresql+")):
            raise RuntimeError("Production requires a PostgreSQL DATABASE_URL.")

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Sign in to view your workspace."
    login_manager.login_message_category = "info"
    login_manager.session_protection = "strong"
    login_manager.init_app(app)
    csrf.init_app(app)

    @app.before_request
    def set_content_security_nonce() -> None:
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def inject_security_nonce() -> dict[str, str]:
        nonce = getattr(g, "csp_nonce", None)
        if nonce is None:
            nonce = secrets.token_urlsafe(16)
            g.csp_nonce = nonce
        return {"csp_nonce": nonce}

    @app.after_request
    def set_security_headers(response):
        nonce = getattr(g, "csp_nonce", "")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "; ".join(
                (
                    "default-src 'self'",
                    "base-uri 'self'",
                    "object-src 'none'",
                    "frame-ancestors 'none'",
                    "form-action 'self'",
                    f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net",
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
                    "img-src 'self' data:",
                    "font-src 'self' data: https://cdn.jsdelivr.net",
                    "connect-src 'self'",
                )
            ),
        )
        if app.config.get("APP_ENV") == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
    app.register_blueprint(main_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(research_bp)
    app.register_blueprint(audits_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(citations_bp)
    app.register_blueprint(reports_bp)
    register_report_commands(app)

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        try:
            user = db.session.get(User, int(user_id))
            return user if user and user.is_active else None
        except (TypeError, ValueError):
            return None

    @app.context_processor
    def inject_owner_email() -> dict[str, str]:
        return {"owner_email": app.config["OWNER_EMAIL"]}

    @app.cli.command("create-admin")
    def create_admin() -> None:
        """Create the initial admin from environment configuration."""
        email = app.config.get("INITIAL_ADMIN_EMAIL")
        password = app.config.get("INITIAL_ADMIN_PASSWORD")
        if not email or not password:
            raise click.ClickException(
                "Set INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD before creating an admin."
            )
        if User.query.filter_by(email=email.strip().lower()).first():
            raise click.ClickException("An account already exists for that email.")
        admin = User(
            email=email,
            full_name="VisibilityForge administrator",
            contact_email=app.config["OWNER_EMAIL"],
        )
        try:
            admin.set_password(password)
        except ValueError as error:
            raise click.ClickException(str(error)) from error
        db.session.add(admin)
        db.session.flush()
        db.session.add(
            ActivityLog(
                user_id=admin.id,
                action="Administrator account created",
                entity_type="user",
                entity_id=admin.id,
            )
        )
        db.session.commit()
        click.echo(f"Administrator account created for {admin.email}.")

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error):
        return render_template("errors/413.html"), 413

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.error("Unhandled application error: %s", error)
        db.session.rollback()
        return render_template("errors/500.html"), 500

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        return render_template("errors/400.html", message=error.description), 400

    logging.getLogger(__name__).info("VisibilityForge AI application initialized")
    return app
