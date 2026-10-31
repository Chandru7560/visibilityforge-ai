import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask import session
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.extensions import db
from app.forms import LoginForm
from app.models import ActivityLog, LoginAttempt, User

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next_url(target: str | None) -> str | None:
    if not target:
        return None
    parsed = urlsplit(target)
    if (
        parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        return None
    return target


def _login_bucket_keys(email: str, remote_addr: str | None) -> tuple[str, str]:
    email_key = hashlib.sha256(f"email:{email.casefold()}".encode()).hexdigest()
    address_key = hashlib.sha256(
        f"address:{remote_addr or 'unknown'}".encode()
    ).hexdigest()
    return email_key, address_key


def _login_is_limited(email_key: str, address_key: str, now: datetime) -> bool:
    cutoff = now - timedelta(
        seconds=current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    )
    db.session.execute(
        delete(LoginAttempt).where(LoginAttempt.window_started_at <= cutoff)
    )
    attempts = db.session.scalars(
        select(LoginAttempt).where(
            LoginAttempt.bucket_key.in_((email_key, address_key)),
            LoginAttempt.window_started_at >= cutoff,
        )
    ).all()
    limits = {
        email_key: current_app.config["LOGIN_RATE_LIMIT_ATTEMPTS"],
        address_key: current_app.config["LOGIN_RATE_LIMIT_IP_ATTEMPTS"],
    }
    return any(item.attempt_count >= limits[item.bucket_key] for item in attempts)


def _record_failed_login(
    bucket_keys: tuple[str, str], now: datetime
) -> None:
    cutoff = now - timedelta(
        seconds=current_app.config["LOGIN_RATE_LIMIT_WINDOW_SECONDS"]
    )
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        insert = postgresql_insert
    elif dialect == "sqlite":
        insert = sqlite_insert
    else:
        raise RuntimeError("Login rate limiting requires PostgreSQL or SQLite.")

    for bucket_key in bucket_keys:
        statement = insert(LoginAttempt).values(
            bucket_key=bucket_key,
            window_started_at=now,
            attempt_count=1,
        )
        db.session.execute(
            statement.on_conflict_do_update(
                index_elements=[LoginAttempt.bucket_key],
                set_={
                    "window_started_at": case(
                        (LoginAttempt.window_started_at <= cutoff, now),
                        else_=LoginAttempt.window_started_at,
                    ),
                    "attempt_count": case(
                        (LoginAttempt.window_started_at <= cutoff, 1),
                        else_=LoginAttempt.attempt_count + 1,
                    ),
                },
            )
        )
    db.session.commit()


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    form = LoginForm()
    if form.validate_on_submit():
        now = datetime.now(timezone.utc)
        email_key, address_key = _login_bucket_keys(
            form.email.data, request.remote_addr
        )
        if _login_is_limited(email_key, address_key, now):
            db.session.commit()
            flash("Too many sign-in attempts. Wait before trying again.", "error")
            return render_template("auth/login.html", form=form), 429

        user = db.session.scalar(select(User).where(User.email == form.email.data))
        if user is None or not user.check_password(form.password.data):
            _record_failed_login((email_key, address_key), now)
            current_app.logger.info("Rejected sign-in attempt")
            flash("Email or password is incorrect.", "error")
        elif not user.is_active:
            _record_failed_login((email_key, address_key), now)
            flash("Email or password is incorrect.", "error")
        else:
            db.session.execute(
                delete(LoginAttempt).where(LoginAttempt.bucket_key == email_key)
            )
            db.session.add(
                ActivityLog(
                    user_id=user.id,
                    action="Signed in",
                    entity_type="user",
                    entity_id=user.id,
                )
            )
            db.session.commit()
            session.clear()
            login_user(user, remember=False, fresh=True)
            flash("Welcome back.", "success")
            return redirect(
                _safe_next_url(request.args.get("next"))
                or url_for("dashboard.index")
            )
    return render_template("auth/login.html", form=form)


@auth_bp.post("/logout")
@login_required
def logout():
    user_id = current_user.id
    db.session.add(
        ActivityLog(
            user_id=user_id,
            action="Signed out",
            entity_type="user",
            entity_id=user_id,
        )
    )
    db.session.commit()
    logout_user()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
