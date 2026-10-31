import hashlib
import json
import re
from io import BytesIO
from types import SimpleNamespace

from app import create_app
from app.config import TestingConfig
from flask import request

from app.extensions import csrf, db
from app.models import AuditIssue, AuditRun, LoginAttempt, Question, User
from app.services.questions import question_export_rows


def _create_user():
    user = User(
        email="release-check@example.com",
        full_name="Release Check",
        contact_email="owner@example.com",
    )
    user.set_password("a-secure-release-password")
    db.session.add(user)
    db.session.commit()
    return user


def _csrf_token(client):
    response = client.get("/auth/login")
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def test_security_headers_and_inline_script_nonce(client, app):
    response = client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    policy = response.headers["Content-Security-Policy"]
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy

    with app.app_context():
        user = _create_user()
        audit = AuditRun(
            user_id=user.id,
            audit_type="technical",
            status="completed",
        )
        db.session.add(audit)
        db.session.flush()
        db.session.add(
            AuditIssue(
                user_id=user.id,
                audit_run_id=audit.id,
                title="Test issue",
                description="Stored for a chart rendering check.",
                severity="low",
                status="open",
            )
        )
        db.session.commit()
    token = _csrf_token(client)
    client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": "release-check@example.com",
            "password": "a-secure-release-password",
        },
    )
    dashboard = client.get("/dashboard/")
    nonce_match = re.search(
        rb"nonce-([^']+)", dashboard.headers["Content-Security-Policy"].encode()
    )
    assert nonce_match
    nonce = nonce_match.group(1)
    assert re.search(rb'<script nonce="' + nonce + rb'"', dashboard.data)


def test_login_rate_limit_persists_hashed_email_and_address_buckets(client, app):
    with app.app_context():
        _create_user()

    csrf_token = _csrf_token(client)
    for _ in range(app.config["LOGIN_RATE_LIMIT_ATTEMPTS"]):
        response = client.post(
            "/auth/login",
            data={
                "csrf_token": csrf_token,
                "email": "release-check@example.com",
                "password": "incorrect-password",
            },
        )
        assert response.status_code == 200

    limited = client.post(
        "/auth/login",
        data={
            "csrf_token": csrf_token,
            "email": "release-check@example.com",
            "password": "a-secure-release-password",
        },
    )
    assert limited.status_code == 429
    assert b"Too many sign-in attempts" in limited.data

    expected_email_key = hashlib.sha256(
        b"email:release-check@example.com"
    ).hexdigest()
    expected_address_key = hashlib.sha256(b"address:127.0.0.1").hexdigest()
    with app.app_context():
        buckets = {
            item.bucket_key: item.attempt_count
            for item in db.session.query(LoginAttempt).all()
        }
        assert buckets == {
            expected_email_key: app.config["LOGIN_RATE_LIMIT_ATTEMPTS"],
            expected_address_key: app.config["LOGIN_RATE_LIMIT_ATTEMPTS"],
        }


def test_login_buckets_do_not_store_email_or_remote_address(client, app):
    with app.app_context():
        _create_user()
    token = _csrf_token(client)
    client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": "release-check@example.com",
            "password": "incorrect-password",
        },
    )
    with app.app_context():
        stored_keys = [row.bucket_key for row in db.session.query(LoginAttempt).all()]
        assert all("release-check" not in key for key in stored_keys)
        assert all("127.0.0.1" not in key for key in stored_keys)


def test_question_csv_escapes_spreadsheet_formula_after_whitespace():
    item = SimpleNamespace(
        question_text="A question",
        topic=None,
        source_url=None,
        source_type=None,
        market="US",
        language=" \t=1+1",
        verification_status="unverified",
    )
    exported = question_export_rows([item])[0]
    assert exported["language"].startswith("'")


def test_successful_llm_request_sends_configured_bearer_key(monkeypatch):
    from app.services import content_drafting

    captured = {}
    response_body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "seo_title": "Evidence-led workflow guide",
                                "meta_description": "A sourced guide for teams.",
                                "body_markdown": "# A guide\n\nReview current evidence.",
                            }
                        )
                    }
                }
            ]
        }
    ).encode()

    def fake_open(request, timeout):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return BytesIO(response_body)

    monkeypatch.setattr(content_drafting, "_open_without_redirects", fake_open)
    draft, warning = content_drafting.generate_content_draft(
        {
            "target_question": "How can teams review workflows?",
            "primary_keyword": "workflow review",
            "search_intent": "informational",
            "target_market": "US",
            "intended_audience": "Editors",
            "content_type": "how_to",
            "generation_mode": "llm",
            "supporting_sources": "",
            "internal_links": "",
        },
        api_key="test-api-secret",
        api_url="https://api.example.test/v1/chat/completions",
    )
    assert captured["authorization"] == "Bearer test-api-secret"
    assert draft["generation_mode"] == "llm"
    assert warning is None


def test_file_database_persists_across_application_instances(tmp_path):
    database_path = (tmp_path / "restart-check.sqlite").as_posix()

    class PersistentTestingConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{database_path}"

    first_app = create_app(PersistentTestingConfig)
    with first_app.app_context():
        db.create_all()
        user = _create_user()
        user_id = user.id

    second_app = create_app(PersistentTestingConfig)
    with second_app.app_context():
        persisted = db.session.get(User, user_id)
        assert persisted is not None
        assert persisted.email == "release-check@example.com"
        db.session.remove()
        db.engine.dispose()


def test_error_handlers_do_not_expose_exception_details_or_default_upload_error(
    client, app
):
    def fail_with_sensitive_detail():
        raise RuntimeError("private database connection detail")

    @csrf.exempt
    def oversized_request():
        return request.get_data()

    app.add_url_rule(
        "/__test/failure",
        endpoint="release_test_failure",
        view_func=fail_with_sensitive_detail,
    )
    app.add_url_rule(
        "/__test/upload",
        endpoint="release_test_upload",
        view_func=oversized_request,
        methods=["POST"],
    )
    app.config["PROPAGATE_EXCEPTIONS"] = False

    failure = client.get("/__test/failure")
    assert failure.status_code == 500
    assert b"private database connection detail" not in failure.data
    assert b"unexpected issue" in failure.data

    too_large = client.post(
        "/__test/upload",
        data=b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1),
        content_type="application/octet-stream",
    )
    assert too_large.status_code == 413
    assert b"exceeds the upload limit" in too_large.data
