import csv
import io
from datetime import datetime, timezone

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import case, desc, select
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.forms import SnippetValidationForm, TechnicalAuditForm
from app.models import ActivityLog, AuditIssue, AuditPage, AuditRun
from app.services.technical_audit import (
    CrawlError,
    UnsafeDestination,
    run_technical_audit,
    validate_public_url,
    validate_snippet,
)

audits_bp = Blueprint("audits", __name__, url_prefix="/audits")
ISSUE_CATEGORIES = (
    "http",
    "metadata",
    "canonical",
    "headings",
    "robots",
    "sitemap",
    "mobile",
    "structured_data",
    "images",
    "redirect",
    "performance",
    "https",
    "internal_links",
    "llms",
    "security",
)
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _owned_run(run_id: int) -> AuditRun:
    run = db.session.scalar(
        select(AuditRun)
        .options(selectinload(AuditRun.pages))
        .where(AuditRun.id == run_id, AuditRun.user_id == current_user.id)
    )
    if run is None:
        abort(404)
    return run


@audits_bp.get("/")
@login_required
def index():
    form = TechnicalAuditForm()
    form.max_pages.data = min(max(current_app.config["AUDIT_MAX_PAGES"], 1), 50)
    form.max_depth.data = min(max(current_app.config["AUDIT_MAX_DEPTH"], 0), 3)
    runs = db.paginate(
        select(AuditRun)
        .where(AuditRun.user_id == current_user.id, AuditRun.audit_type == "technical")
        .order_by(desc(AuditRun.started_at), desc(AuditRun.id)),
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=15,
        error_out=False,
    )
    return render_template("audits/index.html", form=form, runs=runs)


@audits_bp.post("/")
@login_required
def create():
    form = TechnicalAuditForm()
    if not form.validate_on_submit():
        return render_template(
            "audits/index.html",
            form=form,
            runs=db.paginate(
                select(AuditRun)
                .where(
                    AuditRun.user_id == current_user.id,
                    AuditRun.audit_type == "technical",
                )
                .order_by(desc(AuditRun.started_at), desc(AuditRun.id)),
                page=1,
                per_page=15,
                error_out=False,
            ),
        ), 400
    try:
        root_url = validate_public_url(form.website_url.data)
    except UnsafeDestination as error:
        form.website_url.errors.append(str(error))
        return render_template(
            "audits/index.html",
            form=form,
            runs=db.paginate(
                select(AuditRun)
                .where(
                    AuditRun.user_id == current_user.id,
                    AuditRun.audit_type == "technical",
                )
                .order_by(desc(AuditRun.started_at), desc(AuditRun.id)),
                page=1,
                per_page=15,
                error_out=False,
            ),
        ), 400

    max_pages = min(
        max(current_app.config["AUDIT_MAX_PAGES"], 1),
        50,
        form.max_pages.data,
    )
    max_depth = min(
        max(current_app.config["AUDIT_MAX_DEPTH"], 0),
        3,
        form.max_depth.data,
    )
    run = AuditRun(
        user_id=current_user.id,
        audit_type="technical",
        status="running",
        root_url=root_url,
        max_pages=max_pages,
        max_depth=max_depth,
    )
    db.session.add(run)
    db.session.commit()

    try:
        result = run_technical_audit(
            root_url,
            max_pages=max_pages,
            max_depth=max_depth,
            timeout=min(max(float(current_app.config["AUDIT_REQUEST_TIMEOUT"]), 1), 3),
            max_response_bytes=min(
                max(int(current_app.config["AUDIT_MAX_RESPONSE_BYTES"]), 16_384),
                2_000_000,
            ),
            min_request_interval=max(
                float(current_app.config["AUDIT_REQUEST_INTERVAL"]), 0.5
            ),
            max_duration_seconds=min(
                max(float(current_app.config["AUDIT_MAX_DURATION"]), 5), 25
            ),
        )
    except (UnsafeDestination, CrawlError) as error:
        run.status = "failed"
        run.error_summary = str(error)
        run.completed_at = datetime.now(timezone.utc)
        db.session.add(
            AuditIssue(
                user_id=current_user.id,
                audit_run_id=run.id,
                title="Audit could not safely start",
                description=str(error),
                severity="high",
                category="security",
                page_url=root_url,
                evidence="The requested destination was not crawled.",
                recommendation="Use an authorized public website URL and check its public DNS and HTTPS availability.",
                validation_method="Review the URL and confirm all resolved addresses are publicly routable.",
            )
        )
        db.session.commit()
        flash("Audit could not start: the destination did not pass safety checks.", "error")
        return redirect(url_for("audits.detail", run_id=run.id))

    metrics = result["metrics"]
    run.root_url = result["root_url"]
    run.robots_status = metrics["robots_status"]
    run.sitemap_url = metrics["sitemap_url"]
    run.sitemap_status = metrics["sitemap_status"]
    run.llms_status = metrics["llms_status"]
    run.https_available = metrics["https_available"]
    run.error_summary = result["error_summary"]
    for data in result["pages"]:
        db.session.add(
            AuditPage(
                user_id=current_user.id,
                audit_run_id=run.id,
                url=data["url"],
                final_url=data.get("final_url"),
                depth=data.get("depth", 0),
                crawl_status=data.get("crawl_status", "failed"),
                status_code=data.get("status_code"),
                content_type=data.get("content_type"),
                title=(data.get("title") or "")[:1000] or None,
                title_length=data.get("title_length"),
                meta_description=data.get("meta_description"),
                canonical_url=data.get("canonical_url"),
                h1_count=data.get("h1_count", 0),
                internal_links=data.get("internal_links") or [],
                broken_internal_links=data.get("broken_internal_links") or [],
                response_bytes=data.get("response_bytes"),
                response_ms=data.get("response_ms"),
                redirect_chain=data.get("redirect_chain") or [],
                has_viewport=data.get("has_viewport"),
                jsonld_present=data.get("jsonld_present"),
                jsonld_valid=data.get("jsonld_valid"),
                images_total=data.get("images_total"),
                images_missing_alt=data.get("images_missing_alt"),
                error_summary=data.get("error_summary"),
            )
        )
    db.session.flush()
    for item in result["issues"]:
        db.session.add(
            AuditIssue(
                user_id=current_user.id,
                audit_run_id=run.id,
                title=item["title"],
                description=item["description"],
                severity=item["severity"],
                status="open",
                page_url=item["page_url"],
                category=item["category"],
                evidence=item["evidence"],
                recommendation=item["recommendation"],
                code_example=item["code_example"],
                validation_method=item["validation_method"],
                validation_status=item["validation_status"],
            )
        )
    run.pages_crawled = len(result["pages"])
    run.recommendations = result["recommendations"]
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    db.session.add(
        ActivityLog(
            user_id=current_user.id,
            action="Completed technical SEO audit",
            entity_type="audit_run",
            entity_id=run.id,
        )
    )
    db.session.commit()
    return redirect(url_for("audits.detail", run_id=run.id))


@audits_bp.get("/<int:run_id>")
@login_required
def detail(run_id: int):
    run = _owned_run(run_id)
    severity = request.args.get("severity", "")
    category = request.args.get("category", "")
    sort = request.args.get("sort", "severity")
    filters = [AuditIssue.user_id == current_user.id, AuditIssue.audit_run_id == run.id]
    if severity in SEVERITY_ORDER:
        filters.append(AuditIssue.severity == severity)
    if category in ISSUE_CATEGORIES:
        filters.append(AuditIssue.category == category)
    order_column = {
        "severity": AuditIssue.created_at,
        "category": AuditIssue.category,
        "page": AuditIssue.page_url,
        "title": AuditIssue.title,
        "date": AuditIssue.created_at,
    }.get(sort, AuditIssue.created_at)
    issues = db.paginate(
        select(AuditIssue)
        .where(*filters)
        .order_by(
            case(
                (AuditIssue.severity == "critical", 0),
                (AuditIssue.severity == "high", 1),
                (AuditIssue.severity == "medium", 2),
                (AuditIssue.severity == "low", 3),
                else_=4,
            )
            if sort == "severity"
            else order_column.asc(),
            AuditIssue.created_at.desc(),
        ),
        page=max(request.args.get("issues_page", 1, type=int) or 1, 1),
        per_page=25,
        error_out=False,
    )
    severity_counts = {severity_name: 0 for severity_name in SEVERITY_ORDER}
    for issue in run.issues:
        severity_counts[issue.severity] = severity_counts.get(issue.severity, 0) + 1
    snippet_form = SnippetValidationForm()
    page_filter = request.args.get("page_filter", "")
    filtered_pages = [
        page for page in run.pages if page_filter.lower() in page.url.lower()
    ]
    return render_template(
        "audits/detail.html",
        run=run,
        issues=issues,
        pages=filtered_pages,
        severity_counts=severity_counts,
        categories=ISSUE_CATEGORIES,
        selected_severity=severity,
        selected_category=category,
        selected_sort=sort,
        page_filter=page_filter,
        snippet_form=snippet_form,
    )


@audits_bp.get("/issues/<int:issue_id>")
@login_required
def issue_detail(issue_id: int):
    issue = db.session.scalar(
        select(AuditIssue)
        .options(selectinload(AuditIssue.audit_run))
        .where(AuditIssue.id == issue_id, AuditIssue.user_id == current_user.id)
    )
    if issue is None:
        abort(404)
    return render_template("audits/issue_detail.html", issue=issue)


@audits_bp.post("/<int:run_id>/validate-snippet")
@login_required
def validate_run_snippet(run_id: int):
    run = _owned_run(run_id)
    form = SnippetValidationForm()
    if not form.validate_on_submit():
        flash("Choose a supported snippet type and provide a snippet under 50 KB.", "error")
        return redirect(url_for("audits.detail", run_id=run.id))
    result = validate_snippet(form.kind.data, form.snippet.data)
    checks = list(run.validation_checks or [])
    checks.append(
        {
            "kind": form.kind.data,
            "valid": result["valid"],
            "details": result["details"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "validator": "local parser; no external validation",
        }
    )
    run.validation_checks = checks[-50:]
    db.session.commit()
    if result["valid"]:
        flash("Local syntax/metadata checks passed. No external validation was performed.", "success")
    else:
        flash("Local validation found issues: " + " ".join(result["details"]), "error")
    return redirect(url_for("audits.detail", run_id=run.id))


@audits_bp.get("/<int:run_id>/export.csv")
@login_required
def export(run_id: int):
    run = _owned_run(run_id)
    filters = [AuditIssue.user_id == current_user.id, AuditIssue.audit_run_id == run.id]
    severity = request.args.get("severity", "")
    category = request.args.get("category", "")
    if severity in SEVERITY_ORDER:
        filters.append(AuditIssue.severity == severity)
    if category in ISSUE_CATEGORIES:
        filters.append(AuditIssue.category == category)
    issues = db.session.scalars(
        select(AuditIssue).where(*filters).order_by(AuditIssue.created_at, AuditIssue.id)
    ).all()
    fields = (
        "audit_id",
        "root_url",
        "page_url",
        "category",
        "severity",
        "title",
        "evidence",
        "explanation",
        "suggested_correction",
        "code_example",
        "validation_method",
        "validation_status",
        "timestamp_utc",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for issue in issues:
        writer.writerow(
            {
                "audit_id": run.id,
                "root_url": _safe_csv(run.root_url or ""),
                "page_url": _safe_csv(issue.page_url or ""),
                "category": _safe_csv(issue.category),
                "severity": _safe_csv(issue.severity),
                "title": _safe_csv(issue.title),
                "evidence": _safe_csv(issue.evidence or ""),
                "explanation": _safe_csv(issue.description),
                "suggested_correction": _safe_csv(issue.recommendation or ""),
                "code_example": _safe_csv(issue.code_example or ""),
                "validation_method": _safe_csv(issue.validation_method or ""),
                "validation_status": _safe_csv(issue.validation_status),
                "timestamp_utc": issue.created_at.isoformat(),
            }
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="technical-audit-{run.id}.csv"'
        },
    )


def _safe_csv(value: str) -> str:
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
