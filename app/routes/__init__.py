from app.routes.auth import auth_bp
from app.routes.dashboard import dashboard_bp
from app.routes.audits import audits_bp
from app.routes.citations import citations_bp
from app.routes.content import content_bp
from app.routes.health import health_bp
from app.routes.main import main_bp
from app.routes.research import research_bp
from app.routes.reports import reports_bp

__all__ = [
    "auth_bp",
    "audits_bp",
    "citations_bp",
    "content_bp",
    "dashboard_bp",
    "health_bp",
    "main_bp",
    "research_bp",
    "reports_bp",
]
