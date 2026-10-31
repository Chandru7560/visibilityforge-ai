import re
from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    ActivityLog,
    AuditIssue,
    AuditRun,
    CitationTarget,
    ContentPage,
    GrowthTask,
    Measurement,
    Question,
    User,
)


def create_user(email="analyst@example.com", password="a-very-long-test-password"):
    user = User(
        email=email,
        full_name="Test Analyst",
        contact_email="owner@example.com",
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def csrf_token(client, path="/auth/login"):
    response = client.get(path)
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match, "CSRF token should be present in the form."
    return match.group(1).decode()


def sign_in(client, email="analyst@example.com", password="a-very-long-test-password"):
    token = csrf_token(client)
    return client.post(
        "/auth/login",
        data={"csrf_token": token, "email": email, "password": password},
        follow_redirects=True,
    )


def test_dashboard_requires_login(client):
    response = client.get("/dashboard/")

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
    assert client.get("/auth/register").status_code == 404


def test_public_demo_does_not_expose_workspace_data(client, app):
    with app.app_context():
        owner = create_user("private-owner@example.com")
        db.session.add(
            Question(
                user_id=owner.id,
                question_text="Private workspace question marker",
                market="US",
            )
        )
        db.session.commit()
        app.config["OWNER_EMAIL"] = "private-contact@example.com"

    demo = client.get("/demo")
    landing = client.get("/")

    assert demo.status_code == 200
    assert b"PUBLIC, READ-ONLY PREVIEW" in demo.data
    assert b"Illustrative preview" in demo.data
    assert b"Private workspace question marker" not in demo.data
    assert b"private-owner@example.com" not in demo.data
    assert b"private-contact@example.com" not in demo.data
    assert b"Explore the read-only demo" in landing.data
    assert b"private-contact@example.com" not in landing.data
    assert client.get("/dashboard/").status_code == 302
    assert client.get("/research/").status_code == 302


def test_admin_bootstrap_is_environment_configured_and_one_time(app):
    app.config["INITIAL_ADMIN_EMAIL"] = "owner@example.com"
    app.config["INITIAL_ADMIN_PASSWORD"] = "a-bootstrap-test-password"
    runner = app.test_cli_runner()

    created = runner.invoke(args=["create-admin"])
    assert created.exit_code == 0
    assert "Administrator account created for owner@example.com." in created.output

    with app.app_context():
        admin = User.query.filter_by(email="owner@example.com").one()
        assert admin.check_password("a-bootstrap-test-password")
        assert admin.contact_email == "owner@example.com"
        assert admin.activity_logs[0].action == "Administrator account created"

    repeated = runner.invoke(args=["create-admin"])
    assert repeated.exit_code != 0
    assert "An account already exists" in repeated.output


def test_deployment_admin_bootstrap_skips_without_password(app):
    app.config["INITIAL_ADMIN_EMAIL"] = "owner@example.com"
    app.config["INITIAL_ADMIN_PASSWORD"] = None

    result = app.test_cli_runner().invoke(args=["bootstrap-admin"])

    assert result.exit_code == 0
    assert "bootstrap skipped" in result.output
    with app.app_context():
        assert User.query.filter_by(email="owner@example.com").first() is None


def test_deployment_admin_bootstrap_creates_once_and_never_resets_password(app):
    app.config["INITIAL_ADMIN_EMAIL"] = "owner@example.com"
    app.config["INITIAL_ADMIN_PASSWORD"] = "a-bootstrap-test-password"
    runner = app.test_cli_runner()

    created = runner.invoke(args=["bootstrap-admin"])

    assert created.exit_code == 0
    assert "Administrator account created for owner@example.com." in created.output
    assert "a-bootstrap-test-password" not in created.output

    app.config["INITIAL_ADMIN_PASSWORD"] = "a-different-test-password"
    repeated = runner.invoke(args=["bootstrap-admin"])

    assert repeated.exit_code == 0
    assert "account already exists" in repeated.output
    with app.app_context():
        admin = User.query.filter_by(email="owner@example.com").one()
        assert admin.check_password("a-bootstrap-test-password")
        assert not admin.check_password("a-different-test-password")


def test_login_logout_and_csrf_protection(client, app):
    with app.app_context():
        create_user()

    response = sign_in(client)
    assert response.status_code == 200
    assert b"Good to see you, Test." in response.data

    missing_token = client.post("/auth/logout")
    assert missing_token.status_code == 400

    token = csrf_token(client, "/dashboard/")
    signed_out = client.post("/auth/logout", data={"csrf_token": token})
    assert signed_out.status_code == 302
    assert signed_out.headers["Location"].endswith("/auth/login")
    assert client.get("/dashboard/").status_code == 302


def test_login_rejects_bad_password_and_external_redirect(client, app):
    with app.app_context():
        create_user()

    token = csrf_token(client)
    response = client.post(
        "/auth/login?next=https://attacker.example",
        data={
            "csrf_token": token,
            "email": "analyst@example.com",
            "password": "incorrect-password",
        },
    )
    assert response.status_code == 200
    assert b"Email or password is incorrect." in response.data

    token = csrf_token(client)
    response = client.post(
        "/auth/login?next=https://attacker.example",
        data={
            "csrf_token": token,
            "email": "analyst@example.com",
            "password": "a-very-long-test-password",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard/")


def test_dashboard_metrics_use_only_signed_in_users_records(client, app):
    with app.app_context():
        owner = create_user()
        other = create_user("other@example.com")
        question = Question(
            user_id=owner.id,
            question_text="How can a team improve organic reach?",
            market="US",
        )
        db.session.add(question)
        db.session.flush()
        measurement = Measurement(
            user_id=owner.id,
            question_id=question.id,
            collected_at=datetime.now(timezone.utc),
            market="US",
            engine="Example answer engine",
            query="How can a team improve organic reach?",
            raw_answer="A recorded answer for this test.",
            evidence={"excerpt": "A verified citation excerpt."},
            collection_method="manual",
            visibility_score=72.5,
            brand_mentioned=True,
        )
        second_measurement = Measurement(
            user_id=owner.id,
            question_id=question.id,
            collected_at=datetime.now(timezone.utc) - timedelta(days=4),
            market="UK",
            engine="Example search engine",
            query="organic reach for teams",
            raw_answer="A second independently stored answer.",
            evidence=["https://source.example/evidence"],
            collection_method="import",
            visibility_score=65,
        )
        db.session.add_all([measurement, second_measurement])

        audit = AuditRun(user_id=owner.id, audit_type="technical", status="completed")
        db.session.add(audit)
        db.session.flush()
        db.session.add_all(
            [
                AuditIssue(
                    user_id=owner.id,
                    audit_run_id=audit.id,
                    title="Missing page description",
                    description="Recorded audit finding.",
                    severity="critical",
                    status="open",
                ),
                ContentPage(
                    user_id=owner.id,
                    title="Published guide",
                    url="https://brand.example/guide",
                    status="published",
                ),
                CitationTarget(
                    user_id=owner.id,
                    domain="publisher.example",
                    url="https://publisher.example/source",
                    status="opportunity",
                    opportunity_score=83,
                ),
                CitationTarget(
                    user_id=other.id,
                    domain="private-publisher.example",
                    url="https://private-publisher.example/source",
                    status="opportunity",
                ),
                GrowthTask(
                    user_id=owner.id,
                    title="Refresh evidence",
                    status="planned",
                    due_date=date.today() + timedelta(days=30),
                ),
                ActivityLog(user_id=owner.id, action="Recorded a test measurement"),
                Question(
                    user_id=other.id,
                    question_text="Other workspace confidential question",
                    market="CA",
                ),
            ]
        )
        db.session.commit()

    response = sign_in(client)

    assert response.status_code == 200
    assert b"72.5" in response.data
    assert b"1 critical" in response.data
    assert b"private-publisher.example" not in response.data
    assert b"publisher.example" in response.data
    assert b"Other workspace confidential question" not in response.data

    dashboard = client.get("/dashboard/")
    assert dashboard.status_code == 200
    assert b"How can a team improve organic reach?" in dashboard.data
    assert b"Recorded a test measurement" in dashboard.data
    assert b"No visibility measurements yet" not in dashboard.data


def test_dashboard_shows_empty_states_not_fabricated_data(client, app):
    with app.app_context():
        create_user()

    response = sign_in(client)

    assert response.status_code == 200
    assert b"No visibility measurements yet" in response.data
    assert b"No open technical audit issues" in response.data
    assert b"No growth tasks scheduled in the next 90 days." in response.data


def test_measurement_history_is_paginated(client, app):
    with app.app_context():
        user = create_user()
        question = Question(
            user_id=user.id,
            question_text="Pagination test question",
            market="US",
        )
        db.session.add(question)
        db.session.flush()
        for index in range(10):
            db.session.add(
                Measurement(
                    user_id=user.id,
                    question_id=question.id,
                    market="US",
                    engine="Test engine",
                    query=f"pagination query {index}",
                    raw_answer=f"answer {index}",
                    collection_method="manual",
                )
            )
        db.session.commit()

    sign_in(client)
    page_one = client.get("/dashboard/")
    page_two = client.get("/dashboard/?measurements_page=2")

    assert page_one.status_code == page_two.status_code == 200
    assert b"Page 1 of 2" in page_one.data
    assert b"Page 2 of 2" in page_two.data


def test_inactive_accounts_cannot_access_dashboard(client, app):
    with app.app_context():
        user = create_user()
        user.is_active = False
        db.session.commit()

    token = csrf_token(client)
    response = client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": "analyst@example.com",
            "password": "a-very-long-test-password",
        },
    )

    assert response.status_code == 200
    assert b"Email or password is incorrect." in response.data
    assert client.get("/dashboard/").status_code == 302
