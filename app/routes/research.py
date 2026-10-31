import csv
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import selectinload

from app.forms import ENGINES, MARKETS, QUESTION_CATEGORIES, QuestionForm, MeasurementForm
from app.extensions import db
from app.models import ActivityLog, Competitor, CompetitorMention, Measurement, Question
from app.services.measurement_adapters import SUPPORTED_PROFILES
from app.services.questions import (
    QUESTION_CSV_FIELDS,
    QuestionImportError,
    find_duplicate,
    parse_questions_csv,
    question_export_rows,
)
from app.services.visibility import calculate_visibility

research_bp = Blueprint("research", __name__, url_prefix="/research")
MARKET_CODES = {code for code, _label in MARKETS}


def _owned_question(question_id: int) -> Question:
    question = db.session.scalar(
        select(Question)
        .where(Question.id == question_id, Question.user_id == current_user.id)
    )
    if question is None:
        abort(404)
    return question


def _record_activity(action: str, entity_type: str, entity_id: int) -> None:
    db.session.add(
        ActivityLog(
            user_id=current_user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )


@research_bp.get("/")
@login_required
def index():
    filters = [Question.user_id == current_user.id]
    search = request.args.get("q", "").strip()
    topic = request.args.get("topic", "")
    market = request.args.get("market", "")
    verification = request.args.get("verification", "")
    archive = request.args.get("archive", "active")
    if archive == "archived":
        filters.append(Question.is_active.is_(False))
    elif archive != "all":
        filters.append(Question.is_active.is_(True))
    if search:
        filters.append(
            or_(
                Question.question_text.ilike(f"%{search}%"),
                Question.topic.ilike(f"%{search}%"),
            )
        )
    if topic in QUESTION_CATEGORIES:
        filters.append(Question.topic == topic)
    if market in MARKET_CODES:
        filters.append(Question.market == market)
    if verification in {"verified", "unverified", "rejected"}:
        filters.append(Question.verification_status == verification)

    page = max(request.args.get("page", 1, type=int) or 1, 1)
    questions = db.paginate(
        select(Question)
        .where(*filters)
        .order_by(desc(Question.created_at), desc(Question.id)),
        page=page,
        per_page=20,
        error_out=False,
    )
    measurement_counts = dict(
        db.session.execute(
            select(Measurement.question_id, func.count(Measurement.id))
            .where(
                Measurement.user_id == current_user.id,
                Measurement.question_id.in_(
                    [item.id for item in questions.items] or [-1]
                ),
            )
            .group_by(Measurement.question_id)
        ).all()
    )
    active_count = db.session.scalar(
        select(func.count(Question.id)).where(
            Question.user_id == current_user.id, Question.is_active.is_(True)
        )
    ) or 0
    verified_count = db.session.scalar(
        select(func.count(Question.id)).where(
            Question.user_id == current_user.id,
            Question.is_active.is_(True),
            Question.verification_status == "verified",
            Question.source_url.is_not(None),
        )
    ) or 0
    metrics = calculate_visibility(current_user.id)
    return render_template(
        "research/index.html",
        questions=questions,
        measurement_counts=measurement_counts,
        active_count=active_count,
        verified_count=verified_count,
        metrics=metrics,
        search=search,
        selected_topic=topic,
        selected_market=market,
        selected_verification=verification,
        selected_archive=archive,
        categories=QUESTION_CATEGORIES,
        markets=MARKETS,
        engines=[item[0] for item in ENGINES if item[0] != "Other"],
    )


@research_bp.route("/questions/new", methods=["GET", "POST"])
@login_required
def create_question():
    form = QuestionForm()
    if form.validate_on_submit():
        duplicate = find_duplicate(current_user.id, form.question_text.data)
        if duplicate:
            flash(
                f"This question duplicates the existing question #{duplicate.id}. "
                "Open that record to update it instead.",
                "error",
            )
        else:
            question = Question(
                user_id=current_user.id,
                question_text=form.question_text.data,
                topic=form.topic.data,
                market=form.market.data,
                language=form.language.data,
                source_url=form.source_url.data or None,
                source_type=form.source_type.data or None,
                verification_status=form.verification_status.data,
            )
            db.session.add(question)
            db.session.flush()
            _record_activity("Added research question", "question", question.id)
            db.session.commit()
            flash("Question saved.", "success")
            return redirect(url_for("research.question_detail", question_id=question.id))
    return render_template("research/question_form.html", form=form, mode="Create")


@research_bp.route("/questions/<int:question_id>/edit", methods=["GET", "POST"])
@login_required
def edit_question(question_id: int):
    question = _owned_question(question_id)
    if not question.is_active:
        flash("Archived questions are read-only. Restore them to edit.", "info")
        return redirect(url_for("research.question_detail", question_id=question.id))
    form = QuestionForm(obj=question)
    if form.validate_on_submit():
        duplicate = find_duplicate(
            current_user.id, form.question_text.data, exclude_id=question.id
        )
        if duplicate:
            flash(f"This question duplicates question #{duplicate.id}.", "error")
        else:
            question.question_text = form.question_text.data
            question.topic = form.topic.data
            question.market = form.market.data
            question.language = form.language.data
            question.source_url = form.source_url.data or None
            question.source_type = form.source_type.data or None
            question.verification_status = form.verification_status.data
            _record_activity("Updated research question", "question", question.id)
            db.session.commit()
            flash("Question updated.", "success")
            return redirect(url_for("research.question_detail", question_id=question.id))
    return render_template("research/question_form.html", form=form, mode="Edit")


@research_bp.get("/questions/<int:question_id>")
@login_required
def question_detail(question_id: int):
    question = _owned_question(question_id)
    measurement_count = db.session.scalar(
        select(func.count(Measurement.id)).where(
            Measurement.user_id == current_user.id,
            Measurement.question_id == question.id,
        )
    ) or 0
    measurements = db.paginate(
        select(Measurement)
        .where(
            Measurement.user_id == current_user.id,
            Measurement.question_id == question.id,
        )
        .order_by(desc(Measurement.collected_at), desc(Measurement.id)),
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=10,
        error_out=False,
    )
    return render_template(
        "research/question_detail.html",
        question=question,
        measurements=measurements,
        measurement_count=measurement_count,
    )


@research_bp.post("/questions/<int:question_id>/archive")
@login_required
def archive_question(question_id: int):
    question = _owned_question(question_id)
    archive = request.form.get("action") != "restore"
    question.is_active = not archive
    question.archived_at = datetime.now(timezone.utc) if archive else None
    _record_activity(
        "Archived research question" if archive else "Restored research question",
        "question",
        question.id,
    )
    db.session.commit()
    flash("Question archived." if archive else "Question restored.", "success")
    return redirect(url_for("research.question_detail", question_id=question.id))


@research_bp.post("/questions/bulk-archive")
@login_required
def bulk_archive():
    question_ids = {
        int(value)
        for value in request.form.getlist("question_ids")
        if value.isdigit()
    }
    if not question_ids:
        flash("Select at least one question.", "error")
        return redirect(url_for("research.index"))
    questions = db.session.scalars(
        select(Question).where(
            Question.user_id == current_user.id,
            Question.id.in_(question_ids),
            Question.is_active.is_(True),
        )
    ).all()
    timestamp = datetime.now(timezone.utc)
    for question in questions:
        question.is_active = False
        question.archived_at = timestamp
    if questions:
        _record_activity(
            f"Archived {len(questions)} research question(s)",
            "question",
            questions[0].id,
        )
    db.session.commit()
    flash(f"Archived {len(questions)} selected question(s).", "success")
    return redirect(url_for("research.index"))


@research_bp.route("/questions/import", methods=["GET", "POST"])
@login_required
def import_questions():
    if request.method == "POST":
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            flash("Choose a CSV file to import.", "error")
        elif not upload.filename.lower().endswith(".csv"):
            flash("Only CSV files are supported.", "error")
        else:
            try:
                content = upload.stream.read(2_000_001)
                if len(content) > 2_000_000:
                    raise QuestionImportError("CSV file must be smaller than 2 MB.")
                rows = parse_questions_csv(content.decode("utf-8-sig"), current_user.id)
                db.session.add_all(rows)
                db.session.flush()
                _record_activity(f"Imported {len(rows)} research question(s)", "question", rows[0].id)
                db.session.commit()
            except (UnicodeDecodeError, csv.Error, QuestionImportError, ValueError) as error:
                db.session.rollback()
                flash(str(error), "error")
            else:
                flash(f"Imported {len(rows)} questions. Source verification remains as supplied.", "success")
                return redirect(url_for("research.index"))
    return render_template("research/import.html", csv_fields=QUESTION_CSV_FIELDS)


@research_bp.get("/questions/export.csv")
@login_required
def export_questions():
    questions = db.session.scalars(
        select(Question)
        .where(Question.user_id == current_user.id)
        .order_by(Question.id)
    ).all()
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=QUESTION_CSV_FIELDS)
    writer.writeheader()
    for row in question_export_rows(questions):
        writer.writerow(row)
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="questions.csv"'},
    )


@research_bp.get("/measurements/new")
@login_required
def new_measurement():
    form = MeasurementForm()
    questions = db.session.scalars(
        select(Question)
        .where(Question.user_id == current_user.id, Question.is_active.is_(True))
        .order_by(Question.question_text)
    ).all()
    if not questions:
        flash("Add an active question before recording a measurement.", "info")
        return redirect(url_for("research.create_question"))
    form.question_id.choices = [(question.id, question.question_text) for question in questions]
    form.collected_at.data = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")
    return render_template(
        "research/measurement_form.html",
        form=form,
        profiles=SUPPORTED_PROFILES,
    )


@research_bp.post("/measurements")
@login_required
def create_measurement():
    form = MeasurementForm()
    questions = db.session.scalars(
        select(Question)
        .where(Question.user_id == current_user.id, Question.is_active.is_(True))
        .order_by(Question.question_text)
    ).all()
    form.question_id.choices = [(question.id, question.question_text) for question in questions]
    if not form.validate_on_submit():
        return render_template(
            "research/measurement_form.html",
            form=form,
            profiles=SUPPORTED_PROFILES,
        ), 400
    question = db.session.scalar(
        select(Question).where(
            Question.id == form.question_id.data,
            Question.user_id == current_user.id,
            Question.is_active.is_(True),
        )
    )
    if question is None:
        abort(404)
    engine = (form.engine_detail.data or "").strip() if form.engine.data == "Other" else form.engine.data
    try:
        local_time = datetime.fromisoformat(form.collected_at.data)
        if local_time.tzinfo is not None:
            raise ValueError("Enter local time without a UTC offset; select its time zone.")
        observed_at = local_time.replace(tzinfo=ZoneInfo(form.time_zone.data)).astimezone(
            timezone.utc
        )
        cited_urls = [
            line.strip()
            for line in (form.cited_urls.data or "").splitlines()
            if line.strip()
        ]
        if any(
            not value.startswith(("http://", "https://"))
            for value in cited_urls
        ):
            raise ValueError("Every cited URL must start with HTTP:// or HTTPS://.")
        competitor_names = list(
            dict.fromkeys(
                line.strip()
                for line in (form.competitor_names.data or "").splitlines()
                if line.strip()
            )
        )
        measurement = Measurement(
            user_id=current_user.id,
            question_id=question.id,
            collected_at=observed_at,
            market=form.market.data,
            time_zone=form.time_zone.data,
            location_setting=form.location_setting.data,
            engine=engine,
            query=form.query.data.strip(),
            collection_status=form.collection_status.data,
            raw_answer=(form.raw_answer.data or "").strip(),
            evidence={
                "reference": form.screenshot_reference.data.strip()
                if form.screenshot_reference.data
                else None,
                "cited_urls": cited_urls,
            }
            if form.screenshot_reference.data or cited_urls
            else None,
            collection_method=form.collection_method.data,
            brand_mentioned={
                "mentioned": True,
                "not_mentioned": False,
                "not_assessed": None,
            }[form.mention_status.data],
            mention_status=form.mention_status.data,
            mention_context=(form.mention_context.data or "").strip() or None,
            sentiment=form.sentiment.data or None,
            cited_urls=cited_urls or None,
            competitor_names=competitor_names or None,
            google_rank=form.google_rank.data,
            featured_snippet=_optional_bool(form.featured_snippet.data),
            ai_overview=_optional_bool(form.ai_overview.data),
            screenshot_reference=(form.screenshot_reference.data or "").strip() or None,
            visibility_score=form.visibility_score.data,
            error_status=(form.error_status.data or "").strip() or None,
            notes=(form.notes.data or "").strip() or None,
        )
    except (ValueError, TypeError) as error:
        db.session.rollback()
        flash(f"Measurement was not saved: {error}", "error")
        return render_template(
            "research/measurement_form.html",
            form=form,
            profiles=SUPPORTED_PROFILES,
        ), 400

    db.session.add(measurement)
    db.session.flush()
    for name in competitor_names:
        competitor = db.session.scalar(
            select(Competitor).where(
                Competitor.user_id == current_user.id,
                func.lower(Competitor.name) == name.casefold(),
            )
        )
        if competitor is None:
            competitor = Competitor(user_id=current_user.id, name=name)
            db.session.add(competitor)
            db.session.flush()
        db.session.add(
            CompetitorMention(
                user_id=current_user.id,
                competitor_id=competitor.id,
                measurement_id=measurement.id,
                evidence_excerpt=(form.competitor_evidence.data or "").strip() or None,
            )
        )
    _record_activity("Recorded immutable visibility measurement", "measurement", measurement.id)
    db.session.commit()
    flash(
        "Measurement saved as a manual/import observation. No assistant or search engine was queried by this application.",
        "success",
    )
    return redirect(url_for("research.measurement_detail", measurement_id=measurement.id))


def _optional_bool(value: str | None) -> bool | None:
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


@research_bp.get("/measurements")
@login_required
def measurement_history():
    filters = [Measurement.user_id == current_user.id]
    market = request.args.get("market", "")
    engine = request.args.get("engine", "")
    status = request.args.get("status", "")
    question_id = request.args.get("question_id", type=int)
    if market in MARKET_CODES:
        filters.append(Measurement.market == market)
    if engine in SUPPORTED_PROFILES:
        filters.append(Measurement.engine == engine)
    if status in {"complete", "incomplete", "failed"}:
        filters.append(Measurement.collection_status == status)
    if question_id:
        owned_question = db.session.scalar(
            select(Question.id).where(
                Question.id == question_id, Question.user_id == current_user.id
            )
        )
        if owned_question is None:
            abort(404)
        filters.append(Measurement.question_id == question_id)
    questions = db.session.scalars(
        select(Question)
        .where(Question.user_id == current_user.id)
        .order_by(Question.question_text)
    ).all()
    measurements = db.paginate(
        select(Measurement)
        .options(selectinload(Measurement.question))
        .where(*filters)
        .order_by(desc(Measurement.collected_at), desc(Measurement.id)),
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=20,
        error_out=False,
    )
    return render_template(
        "research/measurements.html",
        measurements=measurements,
        markets=MARKETS,
        engines=SUPPORTED_PROFILES,
        selected_market=market,
        selected_engine=engine,
        selected_status=status,
        selected_question=question_id,
        questions=questions,
    )


@research_bp.get("/measurements/<int:measurement_id>")
@login_required
def measurement_detail(measurement_id: int):
    measurement = db.session.scalar(
        select(Measurement)
        .options(selectinload(Measurement.question))
        .where(
            Measurement.id == measurement_id,
            Measurement.user_id == current_user.id,
        )
    )
    if measurement is None:
        abort(404)
    mentions = db.session.scalars(
        select(CompetitorMention)
        .options(selectinload(CompetitorMention.competitor))
        .where(
            CompetitorMention.measurement_id == measurement.id,
            CompetitorMention.user_id == current_user.id,
        )
    ).all()
    return render_template(
        "research/measurement_detail.html",
        measurement=measurement,
        mentions=mentions,
    )


@research_bp.get("/visibility.csv")
@login_required
def export_visibility():
    query = select(Measurement, Question).join(
        Question,
        (Question.id == Measurement.question_id)
        & (Question.user_id == Measurement.user_id),
    ).where(Measurement.user_id == current_user.id)
    market = request.args.get("market", "")
    engine = request.args.get("engine", "")
    if market in MARKET_CODES:
        query = query.where(Measurement.market == market)
    if engine in SUPPORTED_PROFILES:
        query = query.where(Measurement.engine == engine)
    rows = db.session.execute(
        query.order_by(Measurement.collected_at, Measurement.id)
    ).all()
    fields = (
        "measurement_id",
        "question",
        "topic",
        "source_url",
        "source_type",
        "verification_status",
        "collection_date_utc",
        "location",
        "market",
        "engine",
        "collection_method",
        "query",
        "observed_result",
        "mention_status",
        "mention_context",
        "sentiment",
        "cited_urls",
        "competitor_names",
        "competitor_evidence",
        "google_rank",
        "featured_snippet",
        "ai_overview",
        "evidence_reference",
        "collection_status",
        "error_status",
        "notes",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for measurement, question in rows:
        competitor_rows = db.session.scalars(
            select(CompetitorMention)
            .options(selectinload(CompetitorMention.competitor))
            .where(
                CompetitorMention.user_id == current_user.id,
                CompetitorMention.measurement_id == measurement.id,
            )
        ).all()
        writer.writerow(
            {
                "measurement_id": measurement.id,
                "question": _safe_csv_value(question.question_text),
                "topic": _safe_csv_value(question.topic or ""),
                "source_url": _safe_csv_value(question.source_url or ""),
                "source_type": _safe_csv_value(question.source_type or ""),
                "verification_status": _safe_csv_value(question.verification_status),
                "collection_date_utc": measurement.collected_at.isoformat(),
                "location": _safe_csv_value(measurement.location_setting or ""),
                "market": _safe_csv_value(measurement.market),
                "engine": _safe_csv_value(measurement.engine),
                "collection_method": _safe_csv_value(measurement.collection_method),
                "query": _safe_csv_value(measurement.query),
                "observed_result": _safe_csv_value(measurement.raw_answer or ""),
                "mention_status": _safe_csv_value(measurement.mention_status or "not_assessed"),
                "mention_context": _safe_csv_value(measurement.mention_context or ""),
                "sentiment": _safe_csv_value(measurement.sentiment or ""),
                "cited_urls": _safe_csv_value(";".join(measurement.cited_urls or [])),
                "competitor_names": _safe_csv_value(";".join(measurement.competitor_names or [])),
                "competitor_evidence": _safe_csv_value(";".join(
                    item.evidence_excerpt or "" for item in competitor_rows
                )),
                "google_rank": measurement.google_rank or "",
                "featured_snippet": _label_bool(measurement.featured_snippet),
                "ai_overview": _label_bool(measurement.ai_overview),
                "evidence_reference": _safe_csv_value(measurement.screenshot_reference or ""),
                "collection_status": _safe_csv_value(measurement.collection_status),
                "error_status": _safe_csv_value(measurement.error_status or ""),
                "notes": _safe_csv_value(measurement.notes or ""),
            }
        )
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="visibility.csv"'},
    )


def _label_bool(value: bool | None) -> str:
    if value is None:
        return "not_assessed"
    return "yes" if value else "no"


def _safe_csv_value(value: str) -> str:
    if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
        return "'" + value
    return value
