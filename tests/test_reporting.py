import csv
import io
import json
import re
from datetime import date, datetime, timedelta, timezone

from app.extensions import db
from app.models import (
    AuditIssue,
    AuditRun,
    CitationTarget,
    ContentPage,
    GrowthTask,
    Measurement,
    Question,
    User,
    WeeklyReport,
)
from app.services.reporting import build_report, run_report_record


def _create_user():
    user = User(
        email="reporter@example.com",
        full_name="Report Analyst",
        contact_email="owner@example.com",
    )
    user.set_password("a-secure-test-password")
    db.session.add(user)
    db.session.commit()
    return user


def _token(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def _sign_in(client):
    token = _token(client, "/auth/login")
    return client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": "reporter@example.com",
            "password": "a-secure-test-password",
        },
        follow_redirects=True,
    )


def _build_report_records(user_id: int):
    current_time = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    previous_time = datetime(2026, 1, 3, 12, tzinfo=timezone.utc)
    verified = Question(
        user_id=user_id,
        question_text="How can creators choose a content workflow?",
        market="US",
        source_url="https://sources.example.org/verified",
        verification_status="verified",
        created_at=current_time,
    )
    unverified = Question(
        user_id=user_id,
        question_text="Which content workflow is reliable?",
        market="GB",
        created_at=current_time,
    )
    previous_question = Question(
        user_id=user_id,
        question_text="How should editors compare content workflows?",
        market="US",
        source_url="https://sources.example.org/previous",
        verification_status="verified",
        created_at=previous_time,
    )
    db.session.add_all([verified, unverified, previous_question])
    db.session.flush()
    db.session.add_all(
        [
            Measurement(
                user_id=user_id,
                question_id=verified.id,
                collected_at=current_time,
                market="US",
                engine="ChatGPT",
                query=verified.question_text,
                raw_answer="IncuBrix was cited.",
                collection_method="manual",
                mention_status="mentioned",
                cited_urls=["https://brand.example.org"],
                competitor_names=["Competitor A"],
            ),
            Measurement(
                user_id=user_id,
                question_id=unverified.id,
                collected_at=current_time,
                market="GB",
                engine="Gemini",
                query=unverified.question_text,
                raw_answer="No brand was recorded.",
                collection_method="import",
                mention_status="not_mentioned",
            ),
            Measurement(
                user_id=user_id,
                question_id=verified.id,
                collected_at=current_time,
                market="US",
                engine="Claude",
                query=verified.question_text,
                collection_method="manual",
                collection_status="failed",
                raw_answer="",
                error_status="Assistant did not respond.",
            ),
            Measurement(
                user_id=user_id,
                question_id=previous_question.id,
                collected_at=previous_time,
                market="US",
                engine="ChatGPT",
                query=previous_question.question_text,
                raw_answer="IncuBrix was mentioned.",
                collection_method="manual",
                mention_status="mentioned",
                cited_urls=["https://brand.example.org/previous"],
            ),
        ]
    )
    technical_run = AuditRun(
        user_id=user_id,
        audit_type="technical",
        status="completed",
        started_at=current_time,
    )
    failed_audit = AuditRun(
        user_id=user_id,
        audit_type="technical",
        status="failed",
        started_at=current_time,
        error_summary="Blocked during safe crawl startup.",
    )
    content = ContentPage(
        user_id=user_id,
        title="Evidence-led guide",
        url="https://draft.visibilityforge.invalid/content/report-test",
        content_type="brief",
        status="draft",
        created_at=current_time,
        updated_at=current_time,
    )
    published = ContentPage(
        user_id=user_id,
        title="Marked published guide",
        url="https://brand.example.org/guide",
        content_type="article",
        status="published",
        created_at=previous_time,
        updated_at=current_time,
    )
    citation = CitationTarget(
        user_id=user_id,
        domain="publisher.example.org",
        url="https://publisher.example.org/creator-guide",
        source_name="Publisher",
        target_question="Which workflows are useful?",
        relevance_evidence="The page covers creator workflows.",
        evidence_url="https://publisher.example.org/about",
        suggested_angle="Offer a useful evidence-backed contribution.",
        research_status="researched",
        status="monitoring",
        created_at=previous_time,
        updated_at=current_time,
    )
    db.session.add_all([technical_run, failed_audit, content, published, citation])
    db.session.flush()
    issue = AuditIssue(
        user_id=user_id,
        audit_run_id=technical_run.id,
        title="Missing description",
        description="A technical issue found during the period.",
        severity="high",
        status="open",
        created_at=current_time,
    )
    db.session.add(issue)
    db.session.commit()


def test_report_calculations_sample_sizes_evidence_and_period_comparison(app):
    with app.app_context():
        user = _create_user()
        _build_report_records(user.id)
        report = build_report(user.id, date(2026, 1, 8), date(2026, 1, 14))

        assert report["metrics"]["questions_tracked"]["value"] == 2
        assert report["metrics"]["verified_question_sources"]["value"] == 1
        assert report["metrics"]["verified_question_sources"]["denominator"] == 2
        assert report["metrics"]["measurements"]["value"] == 3
        assert report["metrics"]["mention_rate"]["value"] == 50.0
        assert report["metrics"]["mention_rate"]["numerator"] == 1
        assert report["metrics"]["mention_rate"]["denominator"] == 2
        assert report["metrics"]["citation_rate"]["value"] == 50.0
        assert report["metrics"]["competitor_mentions"]["value"][0]["frequency"] == 1
        assert report["metrics"]["technical_issues_by_severity"]["value"]["high"]["value"] == 1
        assert report["metrics"]["content_drafts_created"]["value"] == 1
        assert report["metrics"]["content_pages_marked_published"]["value"] == 1
        assert report["metrics"]["citation_opportunities_researched"]["value"] == 1
        assert report["metrics"]["data_collection_failures"]["value"] == 2
        assert len(report["metrics"]["measurement_coverage"]["value"]) == 3
        assert report["period_comparison"]["mention_rate"]["previous"] == 100.0
        assert report["period_comparison"]["mention_rate"]["change"] == -50.0
        assert report["period_comparison"]["mention_rate"]["previous_sample_size"] == 1
        assert report["metadata"]["comparison_period_available"]
        coverage_claim = next(
            item
            for item in report["evidence_index"]
            if item["claim"] == "measurement_coverage"
        )
        assert coverage_claim["record_count"] == 3


def test_empty_report_returns_zero_or_unavailable_with_noncomparable_period(app):
    with app.app_context():
        user = _create_user()
        report = build_report(user.id, date(2026, 2, 1), date(2026, 2, 7))
        assert report["metrics"]["questions_tracked"]["value"] == 0
        assert report["metrics"]["measurements"]["sample_size"] == 0
        assert report["metrics"]["mention_rate"]["value"] is None
        assert report["metrics"]["citation_rate"]["value"] is None
        assert report["metrics"]["measurement_coverage"]["value"] == []
        assert report["period_comparison"]["mention_rate"]["comparable"] is False
        assert report["period_comparison"]["mention_rate"]["change"] is None
        assert report["metadata"]["comparison_period_available"] is False


def test_report_includes_legacy_boolean_mention_assessments(app):
    with app.app_context():
        user = _create_user()
        question = Question(
            user_id=user.id,
            question_text="How can creators choose a content tool?",
            market="US",
            created_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )
        db.session.add(question)
        db.session.flush()
        db.session.add(
            Measurement(
                user_id=user.id,
                question_id=question.id,
                collected_at=datetime(2026, 5, 10, tzinfo=timezone.utc),
                market="US",
                engine="ChatGPT",
                query=question.question_text,
                raw_answer="Recorded legacy observation.",
                collection_method="manual",
                brand_mentioned=True,
                cited_urls=["https://source.example.org/page"],
            )
        )
        db.session.commit()

        report = build_report(user.id, date(2026, 5, 10), date(2026, 5, 10))
        assert report["metrics"]["mention_rate"]["value"] == 100.0
        assert report["metrics"]["mention_rate"]["denominator"] == 1
        assert report["metrics"]["citation_rate"]["value"] == 100.0


def test_report_run_is_idempotent_and_updates_same_range_record(app):
    with app.app_context():
        user = _create_user()
        first = run_report_record(user.id, date(2026, 3, 1), date(2026, 3, 7))
        first_id = first.id
        assert first.execution_status == "completed"
        assert first.execution_duration_ms is not None
        second = run_report_record(user.id, date(2026, 3, 1), date(2026, 3, 7))
        assert second.id == first_id
        assert WeeklyReport.query.filter_by(user_id=user.id).count() == 1
        assert second.report_key == "range:2026-03-01:2026-03-07"


def test_report_ui_generates_and_exports_tenant_scoped_data(client, app):
    with app.app_context():
        user = _create_user()
        _build_report_records(user.id)
        other = User(
            email="other-reporter@example.com",
            full_name="Other",
            contact_email="owner@example.com",
        )
        other.set_password("a-secure-test-password")
        db.session.add(other)
        db.session.flush()
        db.session.add(
            Question(
                user_id=other.id,
                question_text="Other tenant secret?",
                market="US",
                created_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
            )
        )
        db.session.commit()
    assert _sign_in(client).status_code == 200
    token = _token(client, "/reports/")
    response = client.post(
        "/reports/",
        data={
            "csrf_token": token,
            "start_date": "2026-01-08",
            "end_date": "2026-01-14",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Assessment evidence index" in response.data
    assert b"not causal" in response.data
    with app.app_context():
        reports = WeeklyReport.query.all()
        assert len(reports) == 1
        report_id = reports[0].id
    json_export = client.get(f"/reports/{report_id}.json")
    assert json_export.status_code == 200
    assert 'filename="weekly-report.json"' in json_export.headers["Content-Disposition"]
    assert client.get(f"/reports/{report_id}/html").status_code == 200

    exports = {
        "/reports/exports/visibility.csv": b"measurement_id,question_id,question",
        "/reports/exports/technical-audit.csv": b"issue_id,audit_run_id,page_url",
        "/reports/exports/citation-plan.csv": b"opportunity_id,source_name,url",
        "/reports/exports/content-pages.csv": b"content_page_id,title,seo_title",
        "/reports/exports/growth-plan.csv": b"task_id,week_index,week_start",
    }
    for path, header in exports.items():
        exported = client.get(path)
        assert exported.status_code == 200
        assert header in exported.data
        assert b"Other tenant secret" not in exported.data


def test_generate_plan_has_13_periods_is_idempotent_and_tasks_are_editable(client, app):
    with app.app_context():
        _create_user()
    _sign_in(client)
    page = client.get("/reports/growth-plan")
    assert page.status_code == 200
    assert page.data.count(b'class="plan-period-card"') == 13
    token = _token(client, "/reports/growth-plan")
    first = client.post(
        "/reports/growth-plan/initialize",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert first.status_code == 200
    assert b"Added 13 suggested weekly task" in first.data
    with app.app_context():
        assert GrowthTask.query.count() == 13
        task = GrowthTask.query.filter_by(week_index=1).one()
        task_id = task.id
        week_start = task.week_start
        assert task.target_is_suggested
        assert task.budget_amount is None
        assert task.owner is None
    repeat_token = _token(client, "/reports/growth-plan")
    repeated = client.post(
        "/reports/growth-plan/initialize",
        data={"csrf_token": repeat_token},
        follow_redirects=True,
    )
    assert repeated.status_code == 200
    assert b"Added 0 suggested weekly task" in repeated.data
    with app.app_context():
        assert GrowthTask.query.count() == 13

    edit_token = _token(client, f"/reports/growth-plan/tasks/{task_id}/edit")
    edited = client.post(
        f"/reports/growth-plan/tasks/{task_id}/edit",
        data={
            "csrf_token": edit_token,
            "title": "Confirm measured baseline",
            "description": "Review stored observations and gaps.",
            "week_index": "1",
            "week_start": week_start.isoformat(),
            "owner": "Analyst",
            "tools": "VisibilityForge AI\nSpreadsheet",
            "budget_amount": "125.50",
            "budget_currency": "USD",
            "target_metrics": json.dumps({"measurements": "suggested target"}),
            "target_is_suggested": "y",
            "risks": "Sparse initial sample.",
            "actual_outcome": "Baseline review completed.",
            "priority": "high",
            "status": "done",
            "due_date": (week_start + timedelta(days=6)).isoformat(),
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    with app.app_context():
        saved = db.session.get(GrowthTask, task_id)
        assert saved.title == "Confirm measured baseline"
        assert saved.status == "done"
        assert saved.owner == "Analyst"
        assert saved.budget_amount == 125.5
        assert saved.actual_outcome == "Baseline review completed."
        assert saved.target_is_suggested


def test_content_brief_generator_saves_reusable_content_studio_draft(client, app):
    with app.app_context():
        _create_user()
    _sign_in(client)
    token = _token(client, "/reports/content-brief")
    response = client.post(
        "/reports/content-brief",
        data={
            "csrf_token": token,
            "target_query": "How can teams evaluate content workflows?",
            "search_intent": "informational",
            "target_market": "US",
            "audience": "Small editorial teams",
            "supporting_questions": "What evidence should teams compare?\nHow often should a workflow be reviewed?",
            "source_requirements": "Use current product documentation and independent references.",
            "internal_links": "https://brand.example.org/research",
            "schema_recommendation": "Article; FAQ only when visible page qualifies",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Answer-first opening draft" in response.data
    assert b"What evidence should teams compare?" in response.data
    assert b"Schema recommendation" in response.data
    with app.app_context():
        page = ContentPage.query.one()
        assert page.content_type == "brief"
        assert page.generation_mode == "template"
        assert "Small editorial teams" in page.body_markdown


def test_invalid_report_range_rejected_and_repeat_run_does_not_duplicate(client, app):
    with app.app_context():
        _create_user()
    _sign_in(client)
    token = _token(client, "/reports/")
    invalid = client.post(
        "/reports/",
        data={
            "csrf_token": token,
            "start_date": "2026-04-10",
            "end_date": "2026-04-09",
        },
    )
    assert invalid.status_code == 400
    with app.app_context():
        assert WeeklyReport.query.count() == 0
