import csv
import io
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.forms import CitationOpportunityForm
from app.models import ActivityLog, CitationTarget, Question

citations_bp = Blueprint("citations", __name__, url_prefix="/citations")
SOURCE_TYPES = {
    "article": "Article",
    "directory": "Directory",
    "comparison": "Comparison page",
    "review": "Review site",
    "community": "Community discussion",
}
RESEARCH_STATUSES = {"unresearched", "in_progress", "researched", "dismissed"}


def _owned_opportunity(opportunity_id: int) -> CitationTarget:
    opportunity = db.session.scalar(
        select(CitationTarget).where(
            CitationTarget.id == opportunity_id,
            CitationTarget.user_id == current_user.id,
        )
    )
    if opportunity is None:
        abort(404)
    return opportunity


def _configure_form(form: CitationOpportunityForm) -> None:
    questions = db.session.scalars(
        select(Question)
        .where(
            Question.user_id == current_user.id,
            Question.is_active.is_(True),
        )
        .order_by(Question.question_text)
        .limit(250)
    ).all()
    form.target_question_id.choices = [(0, "No linked question")] + [
        (item.id, f"#{item.id} · {item.question_text[:100]}") for item in questions
    ]


def _record_activity(action: str, opportunity: CitationTarget) -> None:
    db.session.add(
        ActivityLog(
            user_id=current_user.id,
            action=action,
            entity_type="citation_target",
            entity_id=opportunity.id,
        )
    )


def _domain(url: str) -> str:
    hostname = urlsplit(url).hostname
    return (hostname or "").lower()


@citations_bp.get("/")
@login_required
def index():
    filters = [CitationTarget.user_id == current_user.id]
    search = request.args.get("q", "").strip()
    source_type = request.args.get("type", "")
    research_status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    if search:
        filters.append(
            or_(
                CitationTarget.source_name.ilike(f"%{search}%"),
                CitationTarget.domain.ilike(f"%{search}%"),
                CitationTarget.target_question.ilike(f"%{search}%"),
            )
        )
    if source_type in SOURCE_TYPES:
        filters.append(CitationTarget.source_type == source_type)
    if research_status in RESEARCH_STATUSES:
        filters.append(CitationTarget.research_status == research_status)
    if priority in {"low", "medium", "high"}:
        filters.append(CitationTarget.priority == priority)
    opportunities = db.paginate(
        select(CitationTarget)
        .where(*filters)
        .order_by(
            desc(CitationTarget.priority == "high"),
            desc(CitationTarget.updated_at),
            desc(CitationTarget.id),
        ),
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=20,
        error_out=False,
    )
    return render_template(
        "citations/index.html",
        opportunities=opportunities,
        source_types=SOURCE_TYPES,
        selected_type=source_type,
        selected_status=research_status,
        selected_priority=priority,
        search=search,
    )


@citations_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    form = CitationOpportunityForm()
    _configure_form(form)
    question_id = request.args.get("question_id", type=int)
    if request.method == "GET" and question_id:
        question = db.session.scalar(
            select(Question).where(
                Question.id == question_id,
                Question.user_id == current_user.id,
            )
        )
        if question is None:
            abort(404)
        form.target_question.data = question.question_text
        form.target_question_id.data = question.id
    if not form.validate_on_submit():
        return render_template("citations/form.html", form=form, mode="Create"), (
            400 if request.method == "POST" else 200
        )
    question = None
    if form.target_question_id.data:
        question = db.session.scalar(
            select(Question).where(
                Question.id == form.target_question_id.data,
                Question.user_id == current_user.id,
            )
        )
        if question is None:
            abort(404)
    status_map = {
        "unresearched": "opportunity",
        "in_progress": "monitoring",
        "researched": "monitoring",
        "dismissed": "dismissed",
    }
    opportunity = CitationTarget(
        user_id=current_user.id,
        source_name=form.source_name.data.strip(),
        domain=_domain(form.url.data),
        url=form.url.data,
        title=form.source_name.data.strip(),
        status=status_map[form.research_status.data],
        source_type=form.source_type.data,
        target_question=form.target_question.data.strip(),
        target_question_id=question.id if question else None,
        relevance_evidence=(form.relevance_evidence.data or "").strip() or None,
        evidence_url=(form.evidence_url.data or "").strip() or None,
        competitor_reference=(form.competitor_reference.data or "").strip() or None,
        competitor_evidence_url=(form.competitor_evidence_url.data or "").strip() or None,
        suggested_angle=form.suggested_angle.data.strip(),
        priority=form.priority.data,
        research_status=form.research_status.data,
        notes=(form.notes.data or "").strip() or None,
    )
    db.session.add(opportunity)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        form.url.errors.append(
            "This URL is already in your citation workspace. Open the existing record instead."
        )
        return render_template("citations/form.html", form=form, mode="Create"), 400
    _record_activity("Added citation opportunity", opportunity)
    db.session.commit()
    flash("Citation opportunity saved. No mention or competitor reference was independently verified.", "success")
    return redirect(url_for("citations.detail", opportunity_id=opportunity.id))


@citations_bp.get("/<int:opportunity_id>")
@login_required
def detail(opportunity_id: int):
    opportunity = _owned_opportunity(opportunity_id)
    question = None
    if opportunity.target_question_id:
        question = db.session.scalar(
            select(Question).where(
                Question.id == opportunity.target_question_id,
                Question.user_id == current_user.id,
            )
        )
    return render_template(
        "citations/detail.html",
        opportunity=opportunity,
        question=question,
        source_types=SOURCE_TYPES,
    )


@citations_bp.route("/<int:opportunity_id>/edit", methods=["GET", "POST"])
@login_required
def edit(opportunity_id: int):
    opportunity = _owned_opportunity(opportunity_id)
    form = CitationOpportunityForm(obj=opportunity)
    _configure_form(form)
    if request.method == "GET":
        form.target_question_id.data = opportunity.target_question_id or 0
    if not form.validate_on_submit():
        return render_template("citations/form.html", form=form, mode="Edit"), (
            400 if request.method == "POST" else 200
        )
    question = None
    if form.target_question_id.data:
        question = db.session.scalar(
            select(Question).where(
                Question.id == form.target_question_id.data,
                Question.user_id == current_user.id,
            )
        )
        if question is None:
            abort(404)
    opportunity.source_name = form.source_name.data.strip()
    opportunity.title = opportunity.source_name
    opportunity.url = form.url.data
    opportunity.domain = _domain(form.url.data)
    opportunity.source_type = form.source_type.data
    opportunity.target_question = form.target_question.data.strip()
    opportunity.target_question_id = question.id if question else None
    opportunity.relevance_evidence = (form.relevance_evidence.data or "").strip() or None
    opportunity.evidence_url = (form.evidence_url.data or "").strip() or None
    opportunity.competitor_reference = (form.competitor_reference.data or "").strip() or None
    opportunity.competitor_evidence_url = (form.competitor_evidence_url.data or "").strip() or None
    opportunity.suggested_angle = form.suggested_angle.data.strip()
    opportunity.priority = form.priority.data
    opportunity.research_status = form.research_status.data
    opportunity.status = {
        "unresearched": "opportunity",
        "in_progress": "monitoring",
        "researched": "monitoring",
        "dismissed": "dismissed",
    }[form.research_status.data]
    opportunity.notes = (form.notes.data or "").strip() or None
    opportunity.updated_at = datetime.now(timezone.utc)
    _record_activity("Updated citation opportunity", opportunity)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash("That source URL is already saved in your workspace.", "error")
        return redirect(url_for("citations.edit", opportunity_id=opportunity_id))
    flash("Citation opportunity updated.", "success")
    return redirect(url_for("citations.detail", opportunity_id=opportunity.id))


@citations_bp.get("/export.csv")
@login_required
def export_csv():
    query = select(CitationTarget).where(CitationTarget.user_id == current_user.id)
    source_type = request.args.get("type", "")
    research_status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    search = request.args.get("q", "").strip()
    if source_type in SOURCE_TYPES:
        query = query.where(CitationTarget.source_type == source_type)
    if research_status in RESEARCH_STATUSES:
        query = query.where(CitationTarget.research_status == research_status)
    if priority in {"low", "medium", "high"}:
        query = query.where(CitationTarget.priority == priority)
    if search:
        query = query.where(
            or_(
                CitationTarget.source_name.ilike(f"%{search}%"),
                CitationTarget.domain.ilike(f"%{search}%"),
                CitationTarget.target_question.ilike(f"%{search}%"),
            )
        )
    items = db.session.scalars(query.order_by(CitationTarget.id)).all()
    fields = (
        "opportunity_id",
        "source_name",
        "url",
        "source_type",
        "target_question",
        "relevance_evidence",
        "evidence_url",
        "competitor_reference",
        "competitor_evidence_url",
        "suggested_angle",
        "priority",
        "research_status",
        "notes",
        "created_at_utc",
        "updated_at_utc",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in items:
        writer.writerow(
            {
                "opportunity_id": item.id,
                "source_name": _safe_csv(item.source_name or item.title or ""),
                "url": _safe_csv(item.url),
                "source_type": item.source_type,
                "target_question": _safe_csv(item.target_question or ""),
                "relevance_evidence": _safe_csv(item.relevance_evidence or ""),
                "evidence_url": _safe_csv(item.evidence_url or ""),
                "competitor_reference": _safe_csv(item.competitor_reference or ""),
                "competitor_evidence_url": _safe_csv(item.competitor_evidence_url or ""),
                "suggested_angle": _safe_csv(item.suggested_angle or ""),
                "priority": item.priority,
                "research_status": item.research_status,
                "notes": _safe_csv(item.notes or ""),
                "created_at_utc": item.created_at.isoformat(),
                "updated_at_utc": item.updated_at.isoformat(),
            }
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="citation-opportunities.csv"'},
    )


def _safe_csv(value: str) -> str:
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
