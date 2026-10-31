import csv
import io
import json
import logging
from datetime import date, datetime, timedelta, timezone

import click
from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import desc, select
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.forms import ContentBriefForm, GrowthTaskForm, ReportRangeForm
from app.models import (
    AuditIssue,
    AuditRun,
    CitationTarget,
    ContentPage,
    GrowthTask,
    Measurement,
    Question,
    WeeklyReport,
)
from app.services.content_drafting import build_quality_checklist
from app.services.reporting import run_report_record

reports_bp = Blueprint("reports", __name__, url_prefix="/reports")
logger = logging.getLogger(__name__)

PLAN_TASKS = (
    (
        "Establish a measured baseline",
        "Review existing questions, evidence, audits, and visibility measurements. Record coverage gaps without inferring causes.",
        {"baseline_recorded": True},
        "Baseline may be sparse; describe missing records rather than estimating.",
    ),
    (
        "Verify the question inventory",
        "Review question wording, source URLs, category, market, and verification state.",
        {"verified_question_target": 100},
        "The 100-question figure is a suggested inventory target, not completed research.",
    ),
    (
        "Complete source audits",
        "Check each question's source page and retain a reproducible reference.",
        {"source_checks": "track actual completed count"},
        "Third-party pages can move, restrict access, or provide weak evidence.",
    ),
    (
        "Plan a balanced measurement sample",
        "Choose repeatable engines, markets, locations, queries, and collection windows.",
        {"markets": "US plus at least two additional markets"},
        "Manual observations can vary by location, personalization, and time.",
    ),
    (
        "Record first measurement wave",
        "Collect and preserve actual observations, failures, context, and citations.",
        {"measurements": "record actual sample size"},
        "Do not treat incomplete checks as negative mentions.",
    ),
    (
        "Review technical audit evidence",
        "Prioritize observed audit issues and validate fixes locally before deployment.",
        {"critical_high_issues_reviewed": "track actual count"},
        "A crawler cannot observe every page, script-rendered state, or indexing outcome.",
    ),
    (
        "Draft evidence-backed priority pages",
        "Create briefs from verified questions and audit recommendations; cite checked sources.",
        {"briefs_reviewed": "track actual count"},
        "Product claims and source relevance require human review.",
    ),
    (
        "Review legitimate citation prospects",
        "Research suitable third-party publishers and document relevance evidence before outreach.",
        {"researched_opportunities": "track actual count"},
        "A prospect is not a guaranteed mention or link.",
    ),
    (
        "Publish approved content",
        "Publish through the team's authorized CMS after editorial, source, and schema review.",
        {"published_pages": "record confirmed pages only"},
        "This app does not publish to a CMS; record actual publication evidence.",
    ),
    (
        "Repeat the measurement wave",
        "Repeat queries and collection settings where feasible, and keep failed attempts visible.",
        {"repeat_measurements": "record actual sample size"},
        "Comparable collection settings may not be fully controllable.",
    ),
    (
        "Compare periods descriptively",
        "Compare measured samples and coverage; report uncertainty and avoid causal claims.",
        {"period_comparison": "include sample size and dates"},
        "Changes can be influenced by many factors and do not establish attribution.",
    ),
    (
        "Review editorial and technical quality",
        "Update stale sources, resolve verified issues, and document work that remains blocked.",
        {"quality_checks_completed": "track actual count"},
        "Validation on one page does not guarantee site-wide behavior.",
    ),
    (
        "Prepare the 13-week evidence review",
        "Summarize measured outcomes, gaps, completed tasks, budgets, and next-step risks.",
        {"final_review": "record actual outcomes only"},
        "Thirteen planning weeks span 91 calendar days.",
    ),
)


def register_report_commands(app) -> None:
    @app.cli.group("reports")
    def report_commands():
        """Generate evidence-based reports from a repeatable command."""

    @report_commands.command("generate")
    @click.option("--user-id", type=click.IntRange(min=1), required=True)
    @click.option(
        "--start-date",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        required=True,
    )
    @click.option(
        "--end-date",
        type=click.DateTime(formats=["%Y-%m-%d"]),
        required=True,
    )
    def generate_report(user_id: int, start_date: datetime, end_date: datetime):
        if end_date.date() < start_date.date():
            raise click.ClickException("--end-date must be on or after --start-date.")
        if (end_date.date() - start_date.date()).days > 366:
            raise click.ClickException("Report ranges are limited to 367 days.")
        report = run_report_record(user_id, start_date.date(), end_date.date())
        click.echo(
            f"Report {report.id} {report.execution_status}: "
            f"{report.week_start.isoformat()} through "
            f"{(report.period_end or report.week_start).isoformat()} "
            f"({report.execution_duration_ms or 0} ms)."
        )


def _safe_csv(value) -> str:
    text = "" if value is None else str(value)
    if text.lstrip(" \t\r\n").startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def _write_csv(filename: str, fields: tuple[str, ...], rows: list[dict]) -> Response:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _safe_csv(value) for key, value in row.items()})
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _owned_report(report_id: int) -> WeeklyReport:
    report = db.session.scalar(
        select(WeeklyReport).where(
            WeeklyReport.id == report_id,
            WeeklyReport.user_id == current_user.id,
        )
    )
    if report is None:
        abort(404)
    return report


def _owned_task(task_id: int) -> GrowthTask:
    task = db.session.scalar(
        select(GrowthTask).where(
            GrowthTask.id == task_id,
            GrowthTask.user_id == current_user.id,
        )
    )
    if task is None:
        abort(404)
    return task


def _monday(value: date) -> date:
    return value - timedelta(days=value.weekday())


@reports_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    form = ReportRangeForm()
    today = date.today()
    default_end = today
    default_start = today - timedelta(days=6)
    if request.method == "GET":
        form.start_date.data = default_start
        form.end_date.data = default_end
    if form.validate_on_submit():
        try:
            report = run_report_record(
                current_user.id, form.start_date.data, form.end_date.data
            )
        except (
            SQLAlchemyError,
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
        ):
            logger.exception(
                "Report generation failed for user_id=%s",
                current_user.id,
            )
            flash(
                "Report generation failed. The execution was recorded when possible; review application logs and retry.",
                "error",
            )
            return redirect(url_for("reports.index"))
        if report.execution_status == "failed":
            flash(report.error_message or "Report generation failed.", "error")
        else:
            flash("Report generated from stored records.", "success")
        return redirect(url_for("reports.detail", report_id=report.id))
    if request.method == "POST":
        return render_template("reports/index.html", form=form, reports=_recent_reports()), 400
    return render_template("reports/index.html", form=form, reports=_recent_reports())


def _recent_reports():
    return db.session.scalars(
        select(WeeklyReport)
        .where(WeeklyReport.user_id == current_user.id)
        .order_by(desc(WeeklyReport.created_at), desc(WeeklyReport.id))
        .limit(30)
    ).all()


@reports_bp.get("/<int:report_id>")
@login_required
def detail(report_id: int):
    report = _owned_report(report_id)
    return render_template("reports/detail.html", report=report)


@reports_bp.get("/<int:report_id>.json")
@login_required
def report_json(report_id: int):
    report = _owned_report(report_id)
    response = jsonify(
        {
            "report_id": report.id,
            "period_start": report.week_start.isoformat(),
            "period_end": (report.period_end or report.week_start).isoformat(),
            "status": report.execution_status,
            "duration_ms": report.execution_duration_ms,
            "error": report.error_message,
            "metrics": report.metrics,
        }
    )
    response.headers["Content-Disposition"] = 'attachment; filename="weekly-report.json"'
    return response


@reports_bp.post("/<int:report_id>/run-again")
@login_required
def run_again(report_id: int):
    report = _owned_report(report_id)
    updated = run_report_record(
        current_user.id, report.week_start, report.period_end or report.week_start
    )
    flash("Report refreshed in place; the existing scheduled-range record was not duplicated.", "success")
    return redirect(url_for("reports.detail", report_id=updated.id))


@reports_bp.get("/<int:report_id>/html")
@login_required
def readable_report(report_id: int):
    return render_template("reports/detail.html", report=_owned_report(report_id))


@reports_bp.get("/growth-plan")
@login_required
def growth_plan():
    start = _monday(date.today())
    weeks = [
        {
            "index": index,
            "start": start + timedelta(days=(index - 1) * 7),
            "end": start + timedelta(days=(index - 1) * 7 + 6),
        }
        for index in range(1, 14)
    ]
    tasks = db.session.scalars(
        select(GrowthTask)
        .where(
            GrowthTask.user_id == current_user.id,
            GrowthTask.week_start >= weeks[0]["start"],
            GrowthTask.week_start <= weeks[-1]["start"],
        )
        .order_by(GrowthTask.week_start, GrowthTask.priority, GrowthTask.id)
    ).all()
    by_week = {week["start"]: [] for week in weeks}
    for task in tasks:
        by_week.setdefault(task.week_start, []).append(task)
    return render_template("reports/growth_plan.html", weeks=weeks, tasks_by_week=by_week)


@reports_bp.post("/growth-plan/initialize")
@login_required
def initialize_growth_plan():
    start = _monday(date.today())
    existing = set(
        db.session.scalars(
            select(GrowthTask.week_index).where(
                GrowthTask.user_id == current_user.id,
                GrowthTask.week_start >= start,
                GrowthTask.week_start < start + timedelta(days=91),
            )
        ).all()
    )
    created = 0
    for index, (title, description, metrics, risks) in enumerate(PLAN_TASKS, start=1):
        if index in existing:
            continue
        week_start = start + timedelta(days=(index - 1) * 7)
        db.session.add(
            GrowthTask(
                user_id=current_user.id,
                title=title,
                description=description,
                week_index=index,
                week_start=week_start,
                due_date=week_start + timedelta(days=6),
                owner=None,
                tools=["VisibilityForge AI"],
                budget_amount=None,
                budget_currency="USD",
                target_metrics=metrics,
                target_is_suggested=True,
                risks=risks,
                status="planned",
                priority="medium",
            )
        )
        created += 1
    db.session.commit()
    flash(
        f"Added {created} suggested weekly task(s). Targets are planning suggestions, not actual results; review and edit before adopting.",
        "success" if created else "info",
    )
    return redirect(url_for("reports.growth_plan"))


@reports_bp.route("/growth-plan/tasks/new", methods=["GET", "POST"])
@reports_bp.route("/growth-plan/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@login_required
def edit_task(task_id: int | None = None):
    task = _owned_task(task_id) if task_id else None
    form = GrowthTaskForm(obj=task)
    if not task and request.method == "GET":
        week = request.args.get("week", type=int)
        form.week_index.data = week if week and 1 <= week <= 13 else 1
        form.week_start.data = _monday(date.today()) + timedelta(
            days=(form.week_index.data - 1) * 7
        )
        form.status.data = "planned"
        form.priority.data = "medium"
        form.budget_currency.data = "USD"
        form.target_is_suggested.data = True
    if request.method == "GET" and task:
        form.tools.data = "\n".join(task.tools or [])
        form.target_metrics.data = json.dumps(task.target_metrics or {}, indent=2)
    if not form.validate_on_submit():
        return render_template(
            "reports/task_form.html",
            form=form,
            task=task,
        ), 400 if request.method == "POST" else 200
    if task is None:
        task = GrowthTask(user_id=current_user.id, title=form.title.data.strip())
        db.session.add(task)
    task.title = form.title.data.strip()
    task.description = (form.description.data or "").strip() or None
    task.week_index = form.week_index.data
    task.week_start = form.week_start.data
    task.owner = (form.owner.data or "").strip() or None
    task.tools = [item.strip() for item in (form.tools.data or "").splitlines() if item.strip()]
    task.budget_amount = form.budget_amount.data
    task.budget_currency = form.budget_currency.data or "USD"
    task.target_metrics = json.loads(form.target_metrics.data or "{}")
    task.target_is_suggested = form.target_is_suggested.data
    task.risks = (form.risks.data or "").strip() or None
    task.actual_outcome = (form.actual_outcome.data or "").strip() or None
    task.status = form.status.data
    task.priority = form.priority.data
    task.due_date = form.due_date.data
    task.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    flash("Growth task saved. Suggested targets and actual outcomes remain separately labelled.", "success")
    return redirect(url_for("reports.growth_plan"))


@reports_bp.route("/content-brief", methods=["GET", "POST"])
@login_required
def content_brief():
    form = ContentBriefForm()
    form.schema_recommendation.data = (
        form.schema_recommendation.data
        or "Article; FAQPage/HowTo only when visible page content qualifies"
    )
    if not form.validate_on_submit():
        return render_template("reports/brief_form.html", form=form), (
            400 if request.method == "POST" else 200
        )
    question_lines = [
        item.strip()
        for item in (form.supporting_questions.data or "").splitlines()
        if item.strip()
    ]
    links = [
        item.strip()
        for item in (form.internal_links.data or "").splitlines()
        if item.strip()
    ]
    query = form.target_query.data.strip()
    opening = (
        f"For “{query}”, begin by matching the reader's goal to current, documented evidence. "
        "The final answer depends on the sources and market-specific facts checked during editorial review."
    )
    headings = [
        f"# {query}",
        "## Direct answer and key qualification",
        "## Definitions and audience context",
        "## Evidence-backed guidance",
        "### Steps or evaluation criteria",
        "## Limitations and alternatives",
        "## Frequently asked questions",
        "## Sources and review date",
    ]
    body = "\n".join(
        [
            f"# Content brief: {query}",
            "",
            "## Search intent and audience",
            f"- Intent: {form.search_intent.data}",
            f"- Market: {form.target_market.data}",
            f"- Audience: {form.audience.data.strip()}",
            "",
            "## Answer-first opening draft",
            opening,
            "",
            "## Suggested headings",
            *[f"- {heading}" for heading in headings],
            "",
            "## Supporting questions",
            *([f"- {item}" for item in question_lines] or ["- Add supporting questions from verified user research."]),
            "",
            "## Source requirements",
            form.source_requirements.data.strip(),
            "- Verify each source and tie citations to individual factual claims.",
            "- Record the date, market, and limitations for changeable product claims.",
            "",
            "## Internal-link suggestions",
            *([f"- {link}" for link in links] or ["- Identify relevant existing pages; no URLs were supplied."]),
            "",
            "## Schema recommendation",
            form.schema_recommendation.data.strip(),
            "Use FAQPage or HowTo only when the visible page genuinely contains qualifying questions or steps. Structured data does not guarantee rich results.",
            "",
            "## Quality checks",
            "- First two sentences answer the query directly and state important qualifications.",
            "- Verify source relevance, dates, and market coverage.",
            "- Confirm factual claims, internal links, and heading hierarchy.",
            "- Have an editor review; no external validation or publication was performed.",
        ]
    )
    keyword = query[:200]
    page = ContentPage(
        user_id=current_user.id,
        title=f"Brief: {query[:240]}",
        url=f"https://draft.visibilityforge.invalid/content/brief-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}",
        content_type="brief",
        status="draft",
        seo_title=f"{query[:220]}",
        meta_description=(
            f"Evidence-led content brief for {form.target_market.data} audiences. "
            "Review sources and claims before publication."
        )[:320],
        slug="content-brief",
        target_question=query,
        primary_keyword=keyword,
        search_intent=form.search_intent.data,
        target_market=form.target_market.data,
        intended_audience=form.audience.data.strip(),
        body_markdown=body,
        supporting_sources=[],
        internal_links=links,
        schema_markup=None,
        quality_checklist=build_quality_checklist(
            body,
            query,
            f"Evidence-led content brief for {form.target_market.data}.",
            [],
            links,
        ),
        claims_to_verify=["Verify every factual claim and source before publication."],
        generation_mode="template",
    )
    db.session.add(page)
    db.session.commit()
    flash("Reusable content brief saved as a template-based draft.", "success")
    return redirect(url_for("content.preview", page_id=page.id))


@reports_bp.get("/exports/visibility.csv")
@login_required
def export_visibility():
    measurements = db.session.scalars(
        select(Measurement)
        .where(Measurement.user_id == current_user.id)
        .order_by(Measurement.collected_at, Measurement.id)
    ).all()
    questions = {
        item.id: item
        for item in db.session.scalars(
            select(Question).where(Question.user_id == current_user.id)
        ).all()
    }
    fields = (
        "measurement_id", "question_id", "question", "source_url", "verification_status",
        "collected_at_utc", "market", "location", "engine", "collection_method",
        "query", "observed_result", "mention_status", "cited_urls", "competitor_names",
        "collection_status", "error_status", "evidence_reference",
    )
    rows = []
    for item in measurements:
        question = questions.get(item.question_id)
        evidence = item.screenshot_reference or (
            item.evidence if isinstance(item.evidence, str) else json.dumps(item.evidence or {})
        )
        rows.append({
            "measurement_id": item.id,
            "question_id": item.question_id,
            "question": question.question_text if question else "",
            "source_url": question.source_url if question else "",
            "verification_status": question.verification_status if question else "",
            "collected_at_utc": item.collected_at.isoformat(),
            "market": item.market,
            "location": item.location_setting or "",
            "engine": item.engine,
            "collection_method": item.collection_method,
            "query": item.query,
            "observed_result": item.raw_answer,
            "mention_status": item.mention_status or "not_assessed",
            "cited_urls": ";".join(item.cited_urls or []),
            "competitor_names": ";".join(item.competitor_names or []),
            "collection_status": item.collection_status,
            "error_status": item.error_status or "",
            "evidence_reference": evidence,
        })
    return _write_csv("visibility.csv", fields, rows)


@reports_bp.get("/exports/technical-audit.csv")
@login_required
def export_technical_audit():
    issues = db.session.scalars(
        select(AuditIssue)
        .join(AuditRun, AuditRun.id == AuditIssue.audit_run_id)
        .where(
            AuditIssue.user_id == current_user.id,
            AuditRun.user_id == current_user.id,
            AuditRun.audit_type == "technical",
        )
        .order_by(AuditIssue.created_at, AuditIssue.id)
    ).all()
    fields = (
        "issue_id", "audit_run_id", "page_url", "category", "severity", "status",
        "title", "evidence", "explanation", "recommendation", "validation_status",
        "created_at_utc",
    )
    return _write_csv(
        "technical-audit.csv",
        fields,
        [
            {
                "issue_id": item.id,
                "audit_run_id": item.audit_run_id,
                "page_url": item.page_url,
                "category": item.category,
                "severity": item.severity,
                "status": item.status,
                "title": item.title,
                "evidence": item.evidence,
                "explanation": item.description,
                "recommendation": item.recommendation,
                "validation_status": item.validation_status,
                "created_at_utc": item.created_at.isoformat(),
            }
            for item in issues
        ],
    )


@reports_bp.get("/exports/citation-plan.csv")
@login_required
def export_citation_plan():
    items = db.session.scalars(
        select(CitationTarget)
        .where(CitationTarget.user_id == current_user.id)
        .order_by(CitationTarget.id)
    ).all()
    fields = (
        "opportunity_id", "source_name", "url", "source_type", "target_question",
        "relevance_evidence", "evidence_url", "competitor_reference",
        "competitor_evidence_url", "suggested_angle", "priority", "research_status", "notes",
    )
    return _write_csv(
        "citation-plan.csv",
        fields,
        [
            {
                "opportunity_id": item.id,
                "source_name": item.source_name or item.domain,
                "url": item.url,
                "source_type": item.source_type,
                "target_question": item.target_question,
                "relevance_evidence": item.relevance_evidence,
                "evidence_url": item.evidence_url,
                "competitor_reference": item.competitor_reference,
                "competitor_evidence_url": item.competitor_evidence_url,
                "suggested_angle": item.suggested_angle,
                "priority": item.priority,
                "research_status": item.research_status,
                "notes": item.notes,
            }
            for item in items
        ],
    )


@reports_bp.get("/exports/content-pages.csv")
@login_required
def export_content_pages():
    items = db.session.scalars(
        select(ContentPage)
        .where(ContentPage.user_id == current_user.id)
        .order_by(ContentPage.id)
    ).all()
    fields = (
        "content_page_id", "title", "seo_title", "meta_description", "slug",
        "content_type", "status", "target_question", "primary_keyword",
        "search_intent", "target_market", "intended_audience", "generation_mode",
        "research_question_id", "audit_issue_id", "supporting_sources", "internal_links",
        "created_at_utc", "updated_at_utc",
    )
    return _write_csv(
        "content-pages.csv",
        fields,
        [
            {
                "content_page_id": item.id,
                "title": item.title,
                "seo_title": item.seo_title,
                "meta_description": item.meta_description,
                "slug": item.slug,
                "content_type": item.content_type,
                "status": item.status,
                "target_question": item.target_question,
                "primary_keyword": item.primary_keyword,
                "search_intent": item.search_intent,
                "target_market": item.target_market,
                "intended_audience": item.intended_audience,
                "generation_mode": item.generation_mode,
                "research_question_id": item.research_question_id,
                "audit_issue_id": item.audit_issue_id,
                "supporting_sources": ";".join(item.supporting_sources or []),
                "internal_links": ";".join(item.internal_links or []),
                "created_at_utc": item.created_at.isoformat(),
                "updated_at_utc": item.updated_at.isoformat(),
            }
            for item in items
        ],
    )


@reports_bp.get("/exports/growth-plan.csv")
@login_required
def export_growth_plan():
    items = db.session.scalars(
        select(GrowthTask)
        .where(GrowthTask.user_id == current_user.id)
        .order_by(GrowthTask.week_start, GrowthTask.id)
    ).all()
    fields = (
        "task_id", "week_index", "week_start", "title", "description", "owner",
        "tools", "budget_amount", "budget_currency", "target_metrics",
        "target_is_suggested", "risks", "actual_outcome", "priority", "status", "due_date",
    )
    return _write_csv(
        "growth-plan.csv",
        fields,
        [
            {
                "task_id": item.id,
                "week_index": item.week_index,
                "week_start": item.week_start.isoformat() if item.week_start else "",
                "title": item.title,
                "description": item.description,
                "owner": item.owner,
                "tools": ";".join(item.tools or []),
                "budget_amount": item.budget_amount,
                "budget_currency": item.budget_currency,
                "target_metrics": json.dumps(item.target_metrics or {}, ensure_ascii=False),
                "target_is_suggested": item.target_is_suggested,
                "risks": item.risks,
                "actual_outcome": item.actual_outcome,
                "priority": item.priority,
                "status": item.status,
                "due_date": item.due_date.isoformat() if item.due_date else "",
            }
            for item in items
        ],
    )
