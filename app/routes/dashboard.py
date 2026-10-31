from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import desc, func, select
from sqlalchemy.orm import selectinload

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
)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


@dashboard_bp.get("/")
@login_required
def index():
    user_id = current_user.id
    now = datetime.now(timezone.utc)
    trend_start = now - timedelta(days=90)
    today = now.date()
    plan_end = today + timedelta(days=90)

    total_questions = db.session.scalar(
        select(func.count(Question.id)).where(Question.user_id == user_id)
    ) or 0
    evidence_questions = db.session.scalar(
        select(func.count(func.distinct(Measurement.question_id))).where(
            Measurement.user_id == user_id,
            Measurement.evidence.is_not(None),
        )
    ) or 0
    latest_visibility = db.session.execute(
        select(Measurement.visibility_score, Measurement.collected_at)
        .where(
            Measurement.user_id == user_id,
            Measurement.visibility_score.is_not(None),
        )
        .order_by(desc(Measurement.collected_at), desc(Measurement.id))
        .limit(1)
    ).first()

    issue_counts = {
        severity: 0 for severity in ("critical", "high", "medium", "low")
    }
    issue_rows = db.session.execute(
        select(AuditIssue.severity, func.count(AuditIssue.id))
        .join(AuditRun, AuditRun.id == AuditIssue.audit_run_id)
        .where(
            AuditIssue.user_id == user_id,
            AuditIssue.status.in_(("open", "in_progress")),
            AuditRun.audit_type == "technical",
        )
        .group_by(AuditIssue.severity)
    ).all()
    issue_counts.update(dict(issue_rows))

    content_statuses = ("draft", "published", "needs_update", "archived")
    content_counts = {status: 0 for status in content_statuses}
    content_rows = db.session.execute(
        select(ContentPage.status, func.count(ContentPage.id))
        .where(ContentPage.user_id == user_id)
        .group_by(ContentPage.status)
    ).all()
    content_counts.update(dict(content_rows))
    citation_opportunities = db.session.scalar(
        select(func.count(CitationTarget.id)).where(
            CitationTarget.user_id == user_id,
            CitationTarget.status == "opportunity",
        )
    ) or 0
    citations = db.session.scalars(
        select(CitationTarget)
        .where(
            CitationTarget.user_id == user_id,
            CitationTarget.status == "opportunity",
        )
        .order_by(desc(CitationTarget.opportunity_score), CitationTarget.domain)
        .limit(5)
    ).all()

    trend_rows = db.session.execute(
        select(
            func.date(Measurement.collected_at),
            func.avg(Measurement.visibility_score),
        )
        .where(
            Measurement.user_id == user_id,
            Measurement.visibility_score.is_not(None),
            Measurement.collected_at >= trend_start,
        )
        .group_by(func.date(Measurement.collected_at))
        .order_by(func.date(Measurement.collected_at))
    ).all()

    task_rows = db.session.execute(
        select(GrowthTask.status, func.count(GrowthTask.id))
        .where(
            GrowthTask.user_id == user_id,
            GrowthTask.due_date >= today,
            GrowthTask.due_date <= plan_end,
        )
        .group_by(GrowthTask.status)
    ).all()
    plan_counts = dict(task_rows)
    plan_task_count = sum(plan_counts.values())

    measurement_page = max(request.args.get("measurements_page", 1, type=int) or 1, 1)
    activity_page = max(request.args.get("activity_page", 1, type=int) or 1, 1)
    measurements = db.paginate(
        select(Measurement)
        .options(selectinload(Measurement.question))
        .where(Measurement.user_id == user_id)
        .order_by(desc(Measurement.collected_at), desc(Measurement.id)),
        page=measurement_page,
        per_page=8,
        error_out=False,
    )
    activities = db.paginate(
        select(ActivityLog)
        .where(ActivityLog.user_id == user_id)
        .order_by(desc(ActivityLog.created_at), desc(ActivityLog.id)),
        page=activity_page,
        per_page=6,
        error_out=False,
    )

    return render_template(
        "dashboard/index.html",
        total_questions=total_questions,
        evidence_questions=evidence_questions,
        latest_visibility=latest_visibility,
        issue_counts=issue_counts,
        open_issue_count=sum(issue_counts.values()),
        content_counts=content_counts,
        citation_opportunities=citation_opportunities,
        citations=citations,
        trend_labels=[str(row[0]) for row in trend_rows],
        trend_values=[round(float(row[1]), 1) for row in trend_rows],
        trend_has_data=bool(trend_rows),
        plan_counts=plan_counts,
        plan_task_count=plan_task_count,
        plan_end=plan_end,
        now=now,
        measurements=measurements,
        activities=activities,
    )
