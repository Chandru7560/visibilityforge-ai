import json
import re
import uuid
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
from sqlalchemy import desc, func, or_, select

from app.extensions import db
from app.forms import CONTENT_FORMATS, ContentDraftForm, MARKETS
from app.models import ActivityLog, AuditIssue, ContentPage, Question
from app.services.content_drafting import (
    ContentGenerationError,
    build_quality_checklist,
    build_schema_markup,
    generate_content_draft,
)

content_bp = Blueprint("content", __name__, url_prefix="/content")
CONTENT_FORMAT_NAMES = dict(CONTENT_FORMATS)


def _owned_page(page_id: int) -> ContentPage:
    page = db.session.scalar(
        select(ContentPage).where(
            ContentPage.id == page_id, ContentPage.user_id == current_user.id
        )
    )
    if page is None:
        abort(404)
    return page


def _configure_form(form: ContentDraftForm) -> None:
    questions = db.session.scalars(
        select(Question)
        .where(
            Question.user_id == current_user.id,
            Question.is_active.is_(True),
        )
        .order_by(Question.question_text)
        .limit(250)
    ).all()
    issues = db.session.scalars(
        select(AuditIssue)
        .where(
            AuditIssue.user_id == current_user.id,
            AuditIssue.status.in_(("open", "in_progress")),
        )
        .order_by(desc(AuditIssue.created_at))
        .limit(250)
    ).all()
    form.research_question_id.choices = [(0, "No linked question")] + [
        (item.id, f"#{item.id} · {item.question_text[:100]}") for item in questions
    ]
    form.audit_issue_id.choices = [(0, "No linked recommendation")] + [
        (item.id, f"#{item.id} · {item.title[:100]}") for item in issues
    ]


def _url_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _form_data(form: ContentDraftForm) -> dict[str, str]:
    return {
        "target_question": form.target_question.data.strip(),
        "primary_keyword": form.primary_keyword.data.strip(),
        "search_intent": form.search_intent.data,
        "target_market": form.target_market.data,
        "intended_audience": form.intended_audience.data.strip(),
        "content_type": form.content_type.data,
        "generation_mode": form.generation_mode.data,
        "supporting_sources": form.supporting_sources.data or "",
        "internal_links": form.internal_links.data or "",
    }


def _fill_editor_form(form: ContentDraftForm, page: ContentPage) -> None:
    form.target_question.data = page.target_question or page.title
    form.primary_keyword.data = page.primary_keyword or page.title
    form.search_intent.data = page.search_intent or "informational"
    form.target_market.data = (
        page.target_market if page.target_market in dict(MARKETS) else "US"
    )
    form.intended_audience.data = page.intended_audience or "General readers"
    form.content_type.data = (
        page.content_type if page.content_type in CONTENT_FORMAT_NAMES else "brief"
    )
    form.generation_mode.data = page.generation_mode
    form.supporting_sources.data = "\n".join(page.supporting_sources or [])
    form.internal_links.data = "\n".join(page.internal_links or [])
    form.seo_title.data = page.seo_title or page.title
    form.meta_description.data = page.meta_description or ""
    form.slug.data = page.slug or ""
    form.body_markdown.data = page.body_markdown or ""
    form.status.data = page.status if page.status != "archived" else "draft"
    form.research_question_id.data = page.research_question_id or 0
    form.audit_issue_id.data = page.audit_issue_id or 0


def _validate_link_ownership(question_id: int, issue_id: int) -> tuple[Question | None, AuditIssue | None]:
    question = None
    issue = None
    if question_id:
        question = db.session.scalar(
            select(Question).where(
                Question.id == question_id,
                Question.user_id == current_user.id,
            )
        )
        if question is None:
            abort(404)
    if issue_id:
        issue = db.session.scalar(
            select(AuditIssue).where(
                AuditIssue.id == issue_id,
                AuditIssue.user_id == current_user.id,
            )
        )
        if issue is None:
            abort(404)
    return question, issue


def _record_activity(action: str, page: ContentPage) -> None:
    db.session.add(
        ActivityLog(
            user_id=current_user.id,
            action=action,
            entity_type="content_page",
            entity_id=page.id,
        )
    )


@content_bp.get("/")
@login_required
def index():
    filters = [ContentPage.user_id == current_user.id]
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    content_type = request.args.get("type", "")
    if status in {"draft", "published", "needs_update", "archived"}:
        filters.append(ContentPage.status == status)
    if content_type in CONTENT_FORMAT_NAMES:
        filters.append(ContentPage.content_type == content_type)
    if search:
        filters.append(
            or_(
                ContentPage.title.ilike(f"%{search}%"),
                ContentPage.target_question.ilike(f"%{search}%"),
                ContentPage.primary_keyword.ilike(f"%{search}%"),
            )
        )
    pages = db.paginate(
        select(ContentPage)
        .where(*filters)
        .order_by(desc(ContentPage.updated_at), desc(ContentPage.id)),
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=20,
        error_out=False,
    )
    totals = {
        "drafts": db.session.scalar(
            select(func.count(ContentPage.id)).where(
                ContentPage.user_id == current_user.id,
                ContentPage.status == "draft",
            )
        )
        or 0,
        "published": db.session.scalar(
            select(func.count(ContentPage.id)).where(
                ContentPage.user_id == current_user.id,
                ContentPage.status == "published",
            )
        )
        or 0,
        "archived": db.session.scalar(
            select(func.count(ContentPage.id)).where(
                ContentPage.user_id == current_user.id,
                ContentPage.status == "archived",
            )
        )
        or 0,
    }
    return render_template(
        "content/index.html",
        pages=pages,
        totals=totals,
        search=search,
        selected_status=status,
        selected_type=content_type,
        content_formats=CONTENT_FORMATS,
    )


@content_bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    form = ContentDraftForm()
    _configure_form(form)
    linked_question_id = request.args.get("question_id", type=int)
    linked_issue_id = request.args.get("issue_id", type=int)
    if request.method == "GET":
        if linked_question_id:
            question = db.session.scalar(
                select(Question).where(
                    Question.id == linked_question_id,
                    Question.user_id == current_user.id,
                )
            )
            if question is None:
                abort(404)
            form.target_question.data = question.question_text
            form.primary_keyword.data = question.topic or ""
            form.target_market.data = (
                question.market if question.market in dict(MARKETS) else "US"
            )
            form.research_question_id.data = question.id
            form.content_type.data = "faq"
            if question.verification_status == "verified" and question.source_url:
                form.supporting_sources.data = question.source_url
        if linked_issue_id:
            issue = db.session.scalar(
                select(AuditIssue).where(
                    AuditIssue.id == linked_issue_id,
                    AuditIssue.user_id == current_user.id,
                )
            )
            if issue is None:
                abort(404)
            form.target_question.data = issue.title
            form.primary_keyword.data = "technical SEO"
            form.intended_audience.data = "Website owners and content editors"
            form.content_type.data = "brief"
            form.audit_issue_id.data = issue.id
    if not form.validate_on_submit():
        return render_template("content/form.html", form=form, mode="Create"), (
            400 if request.method == "POST" else 200
        )

    question, issue = _validate_link_ownership(
        form.research_question_id.data or 0, form.audit_issue_id.data or 0
    )
    data = _form_data(form)
    if issue:
        data["audit_context"] = (
            f"Finding: {issue.title}\n"
            f"Explanation: {issue.description}\n"
            f"Observed evidence: {issue.evidence or 'No evidence excerpt recorded.'}\n"
            f"Suggested correction: {issue.recommendation or 'No correction recorded.'}\n"
            f"Validation method: {issue.validation_method or 'Review the affected page and verify the change.'}"
        )
    try:
        generated, warning = generate_content_draft(
            data,
            api_key=current_app.config.get("CONTENT_LLM_API_KEY"),
            api_url=current_app.config["CONTENT_LLM_API_URL"],
            model=current_app.config["CONTENT_LLM_MODEL"],
            timeout=current_app.config["CONTENT_LLM_TIMEOUT"],
        )
    except ContentGenerationError as error:
        flash(str(error), "error")
        return render_template("content/form.html", form=form, mode="Create"), 503

    sources = _url_lines(form.supporting_sources.data)
    links = _url_lines(form.internal_links.data)
    slug = re.sub(r"[^a-z0-9-]", "", (form.slug.data or generated["slug"]).casefold())
    slug = slug[:220].strip("-") or generated["slug"]
    title = generated["seo_title"]
    schema_data = {
        "content_type": form.content_type.data,
        "target_question": form.target_question.data.strip(),
    }
    page = ContentPage(
        user_id=current_user.id,
        title=title[:250],
        url=f"https://draft.visibilityforge.invalid/content/{slug}-{uuid.uuid4().hex[:8]}",
        status=form.status.data,
        content_type=form.content_type.data,
        seo_title=generated["seo_title"],
        meta_description=generated["meta_description"],
        slug=slug,
        target_question=form.target_question.data.strip(),
        primary_keyword=form.primary_keyword.data.strip(),
        search_intent=form.search_intent.data,
        target_market=form.target_market.data,
        intended_audience=form.intended_audience.data.strip(),
        body_markdown=generated["body_markdown"],
        supporting_sources=sources,
        internal_links=links,
        schema_markup=build_schema_markup(
            schema_data, generated["seo_title"], generated["meta_description"]
        ),
        quality_checklist=build_quality_checklist(
            generated["body_markdown"],
            generated["seo_title"],
            generated["meta_description"],
            sources,
            links,
        ),
        claims_to_verify=[
            "Verify every product feature, pricing, availability, and comparative claim against current primary sources.",
            "Confirm that each supplied source supports the claim it accompanies.",
        ],
        generation_mode=generated["generation_mode"],
        research_question_id=question.id if question else None,
        audit_issue_id=issue.id if issue else None,
    )
    db.session.add(page)
    db.session.flush()
    _record_activity("Generated content draft", page)
    db.session.commit()
    if warning:
        flash(warning, "info")
    elif page.generation_mode == "template":
        flash("Saved a template-based draft. No external AI model was used.", "success")
    else:
        flash("Saved an LLM-assisted draft. Review all claims and sources before publication.", "success")
    return redirect(url_for("content.preview", page_id=page.id))


@content_bp.get("/<int:page_id>")
@login_required
def preview(page_id: int):
    page = _owned_page(page_id)
    return render_template(
        "content/preview.html",
        page=page,
        schema_json=json.dumps(page.schema_markup, indent=2, ensure_ascii=False)
        if page.schema_markup
        else None,
    )


@content_bp.route("/<int:page_id>/edit", methods=["GET", "POST"])
@login_required
def edit(page_id: int):
    page = _owned_page(page_id)
    if page.status == "archived":
        flash("Restore an archived draft by duplicating it before editing.", "info")
        return redirect(url_for("content.preview", page_id=page.id))
    form = ContentDraftForm(obj=page)
    _configure_form(form)
    if request.method == "GET":
        _fill_editor_form(form, page)
    if not form.validate_on_submit():
        return render_template("content/form.html", form=form, mode="Edit"), (
            400 if request.method == "POST" else 200
        )
    question, issue = _validate_link_ownership(
        form.research_question_id.data or 0, form.audit_issue_id.data or 0
    )
    sources = _url_lines(form.supporting_sources.data)
    links = _url_lines(form.internal_links.data)
    page.title = form.seo_title.data.strip()[:250] or page.title
    page.seo_title = form.seo_title.data.strip() or None
    page.meta_description = form.meta_description.data.strip() or None
    page.slug = re.sub(
        r"[^a-z0-9-]", "", form.slug.data.strip().casefold()
    )[:220].strip("-") or None
    page.url = (
        f"https://draft.visibilityforge.invalid/content/"
        f"{page.slug or 'draft'}-{page.id}"
    )
    page.target_question = form.target_question.data.strip()
    page.primary_keyword = form.primary_keyword.data.strip()
    page.search_intent = form.search_intent.data
    page.target_market = form.target_market.data
    page.intended_audience = form.intended_audience.data.strip()
    page.content_type = form.content_type.data
    page.body_markdown = form.body_markdown.data
    page.supporting_sources = sources
    page.internal_links = links
    page.status = form.status.data
    page.research_question_id = question.id if question else None
    page.audit_issue_id = issue.id if issue else None
    page.schema_markup = build_schema_markup(
        {
            "content_type": page.content_type,
            "target_question": page.target_question,
        },
        page.seo_title or page.title,
        page.meta_description or "",
    )
    page.quality_checklist = build_quality_checklist(
        page.body_markdown or "",
        page.seo_title or page.title,
        page.meta_description or "",
        sources,
        links,
    )
    page.updated_at = datetime.now(timezone.utc)
    _record_activity("Edited content draft", page)
    db.session.commit()
    flash("Draft changes saved. A status change does not publish content to your website.", "success")
    return redirect(url_for("content.preview", page_id=page.id))


@content_bp.post("/<int:page_id>/duplicate")
@login_required
def duplicate(page_id: int):
    original = _owned_page(page_id)
    copy = ContentPage(
        user_id=current_user.id,
        title=f"{original.title[:240]} (copy)",
        url=f"https://draft.visibilityforge.invalid/content/{original.slug or 'draft'}-{uuid.uuid4().hex[:8]}",
        status="draft",
        content_type=original.content_type,
        seo_title=original.seo_title,
        meta_description=original.meta_description,
        slug=f"{original.slug or 'draft'}-copy",
        target_question=original.target_question,
        primary_keyword=original.primary_keyword,
        search_intent=original.search_intent,
        target_market=original.target_market,
        intended_audience=original.intended_audience,
        body_markdown=original.body_markdown,
        supporting_sources=list(original.supporting_sources or []),
        internal_links=list(original.internal_links or []),
        schema_markup=original.schema_markup,
        quality_checklist=original.quality_checklist,
        claims_to_verify=original.claims_to_verify,
        generation_mode=original.generation_mode,
        research_question_id=original.research_question_id,
        audit_issue_id=original.audit_issue_id,
    )
    db.session.add(copy)
    db.session.flush()
    _record_activity("Duplicated content draft", copy)
    db.session.commit()
    flash("Draft duplicated.", "success")
    return redirect(url_for("content.edit", page_id=copy.id))


@content_bp.post("/<int:page_id>/archive")
@login_required
def archive(page_id: int):
    page = _owned_page(page_id)
    page.status = "archived"
    page.archived_at = datetime.now(timezone.utc)
    _record_activity("Archived content draft", page)
    db.session.commit()
    flash("Draft archived.", "success")
    return redirect(url_for("content.index"))


@content_bp.get("/<int:page_id>/export.md")
@login_required
def export_markdown(page_id: int):
    page = _owned_page(page_id)
    body = (
        f"# {page.seo_title or page.title}\n\n"
        f"**Meta description:** {page.meta_description or ''}\n\n"
        f"**Slug:** {page.slug or ''}\n\n"
        f"**Status:** {page.status} · **Draft mode:** {page.generation_mode}\n\n"
        f"{page.body_markdown or ''}\n"
    )
    return Response(
        body,
        mimetype="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="content-draft-{page.id}.md"'
        },
    )


@content_bp.get("/<int:page_id>/schema.json")
@login_required
def export_schema(page_id: int):
    page = _owned_page(page_id)
    if not page.schema_markup:
        abort(404)
    return Response(
        json.dumps(page.schema_markup, indent=2, ensure_ascii=False),
        mimetype="application/ld+json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="content-schema-{page.id}.jsonld"'
        },
    )


@content_bp.post("/generate-ten")
@login_required
def generate_ten():
    candidates = db.session.scalars(
        select(Question)
        .where(
            Question.user_id == current_user.id,
            Question.is_active.is_(True),
            Question.verification_status == "verified",
            Question.source_url.is_not(None),
        )
        .order_by(Question.id)
    ).all()
    existing_ids = set(
        db.session.scalars(
            select(ContentPage.research_question_id).where(
                ContentPage.user_id == current_user.id,
                ContentPage.research_question_id.is_not(None),
                ContentPage.status != "archived",
            )
        ).all()
    )
    created = 0
    for question in (item for item in candidates if item.id not in existing_ids):
        if created >= 10:
            break
        data = {
            "target_question": question.question_text,
            "primary_keyword": question.topic or question.question_text[:200],
            "search_intent": "informational",
            "target_market": question.market if question.market in dict(MARKETS) else "US",
            "intended_audience": "Content creators researching this question",
            "content_type": "faq",
            "generation_mode": "template",
            "supporting_sources": question.source_url,
            "internal_links": "",
        }
        generated, _ = generate_content_draft(data)
        sources = [question.source_url]
        page = ContentPage(
            user_id=current_user.id,
            title=generated["seo_title"],
            url=f"https://draft.visibilityforge.invalid/content/{generated['slug']}-{uuid.uuid4().hex[:8]}",
            content_type="faq",
            seo_title=generated["seo_title"],
            meta_description=generated["meta_description"],
            slug=generated["slug"],
            target_question=question.question_text,
            primary_keyword=data["primary_keyword"],
            search_intent="informational",
            target_market=data["target_market"],
            intended_audience=data["intended_audience"],
            body_markdown=generated["body_markdown"],
            supporting_sources=sources,
            internal_links=[],
            schema_markup=build_schema_markup(
                data, generated["seo_title"], generated["meta_description"]
            ),
            quality_checklist=build_quality_checklist(
                generated["body_markdown"],
                generated["seo_title"],
                generated["meta_description"],
                sources,
                [],
            ),
            claims_to_verify=[
                "Verify every product, pricing, availability, and comparative claim before publication.",
                "Confirm this source supports each claim; source URLs are user-provided and not fetched by this generator.",
            ],
            generation_mode="template",
            research_question_id=question.id,
        )
        db.session.add(page)
        db.session.flush()
        _record_activity("Generated source-backed template draft", page)
        existing_ids.add(question.id)
        created += 1
    db.session.commit()
    if created == 10:
        flash("Generated 10 source-linked template drafts from your verified questions. Review source support and product claims before publication.", "success")
    elif created:
        flash(f"Generated {created} source-linked draft(s); only {created} eligible verified questions without an existing draft were available.", "info")
    else:
        flash("No eligible questions found. Add at least one active verified question with a real source URL; no example sources were invented.", "info")
    return redirect(url_for("content.index"))
