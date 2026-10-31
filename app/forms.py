import json
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlsplit

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    FloatField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional, ValidationError


def validate_email(_form, field) -> None:
    email = field.data.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValidationError("Enter a valid email address.")
    field.data = email


def validate_http_url(_form, field) -> None:
    if not field.data:
        return
    value = field.data.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValidationError("Enter a full HTTP or HTTPS URL.") from None
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in value)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValidationError("Enter a full HTTP or HTTPS URL.")
    field.data = value


class LoginForm(FlaskForm):
    email = StringField(
        "Email address",
        validators=[DataRequired(), Length(max=254), validate_email],
    )
    password = PasswordField("Password", validators=[DataRequired(), Length(max=1024)])
    submit = SubmitField("Sign in")


class TechnicalAuditForm(FlaskForm):
    website_url = StringField(
        "Public website URL",
        validators=[DataRequired(), Length(max=2048), validate_http_url],
        description="Only public HTTP/HTTPS destinations on standard ports are allowed.",
    )
    max_pages = IntegerField(
        "Maximum pages",
        default=20,
        validators=[DataRequired()],
        description="Capped by the application at 50 pages.",
    )
    max_depth = IntegerField(
        "Maximum link depth",
        default=2,
        validators=[DataRequired()],
        description="Capped by the application at depth 3.",
    )
    terms_confirmed = BooleanField(
        "I am authorized to audit this website and have reviewed its terms.",
        validators=[DataRequired(message="Confirm authorization and review site terms.")],
    )
    submit = SubmitField("Start technical audit")

    def validate_max_pages(self, field) -> None:
        if field.data is not None and not 1 <= field.data <= 50:
            raise ValidationError("Choose between 1 and 50 pages.")

    def validate_max_depth(self, field) -> None:
        if field.data is not None and not 0 <= field.data <= 3:
            raise ValidationError("Choose a link depth between 0 and 3.")


class SnippetValidationForm(FlaskForm):
    kind = SelectField(
        "Snippet type",
        choices=[
            ("jsonld", "JSON-LD"),
            ("html_metadata", "HTML metadata"),
        ],
        validators=[DataRequired()],
    )
    snippet = TextAreaField(
        "Generated snippet",
        validators=[DataRequired(), Length(max=50_000)],
    )
    submit = SubmitField("Validate locally")


MARKETS = [
    ("US", "United States"),
    ("GB", "United Kingdom"),
    ("CA", "Canada"),
    ("AU", "Australia"),
    ("IN", "India"),
]

CONTENT_FORMATS = [
    ("how_to", "How-to guide"),
    ("comparison", "Product comparison"),
    ("faq", "FAQ page"),
    ("glossary", "Glossary page"),
    ("brief", "Content brief"),
]

SEARCH_INTENTS = [
    ("informational", "Informational"),
    ("commercial", "Commercial investigation"),
    ("transactional", "Transactional"),
    ("navigational", "Navigational"),
]


class ContentDraftForm(FlaskForm):
    target_question = StringField(
        "Target question", validators=[DataRequired(), Length(max=500)]
    )
    primary_keyword = StringField(
        "Primary keyword", validators=[DataRequired(), Length(max=250)]
    )
    search_intent = SelectField(
        "Search intent", choices=SEARCH_INTENTS, validators=[DataRequired()]
    )
    target_market = SelectField(
        "Target market", choices=MARKETS, validators=[DataRequired()]
    )
    intended_audience = StringField(
        "Intended audience", validators=[DataRequired(), Length(max=250)]
    )
    supporting_sources = TextAreaField(
        "Supporting source URLs (one per line)",
        validators=[Length(max=10000)],
        description="Use real, checked URLs. Supplied sources are not independently verified.",
    )
    internal_links = TextAreaField(
        "Internal link URLs (one per line)", validators=[Length(max=10000)]
    )
    content_type = SelectField(
        "Desired content format", choices=CONTENT_FORMATS, validators=[DataRequired()]
    )
    generation_mode = SelectField(
        "Drafting mode",
        choices=[
            ("template", "Template-based (no external AI)"),
            ("llm", "Optional external LLM"),
        ],
        default="template",
        validators=[DataRequired()],
    )
    research_question_id = SelectField(
        "Linked research question (optional)",
        coerce=int,
        choices=[(0, "No linked question")],
        validators=[Optional()],
    )
    audit_issue_id = SelectField(
        "Linked audit recommendation (optional)",
        coerce=int,
        choices=[(0, "No linked recommendation")],
        validators=[Optional()],
    )
    seo_title = StringField("SEO title", validators=[Length(max=250)])
    meta_description = StringField(
        "Meta description", validators=[Length(max=320)]
    )
    slug = StringField("URL slug", validators=[Length(max=250)])
    body_markdown = TextAreaField(
        "Draft content (Markdown)", validators=[Length(max=50000)]
    )
    status = SelectField(
        "Publication status",
        choices=[
            ("draft", "Draft"),
            ("needs_update", "Needs update"),
            ("published", "Marked published"),
        ],
        default="draft",
        validators=[DataRequired()],
    )
    submit = SubmitField("Generate and save draft")

    def validate_supporting_sources(self, field) -> None:
        _validate_url_lines(field)

    def validate_internal_links(self, field) -> None:
        _validate_url_lines(field)


class CitationOpportunityForm(FlaskForm):
    source_name = StringField(
        "Source name", validators=[DataRequired(), Length(max=250)]
    )
    url = StringField("Source URL", validators=[DataRequired(), Length(max=2048), validate_http_url])
    source_type = SelectField(
        "Source type",
        choices=[
            ("article", "Article"),
            ("directory", "Directory"),
            ("comparison", "Comparison page"),
            ("review", "Review site"),
            ("community", "Community discussion"),
        ],
        validators=[DataRequired()],
    )
    target_question = StringField(
        "Target question", validators=[DataRequired(), Length(max=500)]
    )
    target_question_id = SelectField(
        "Linked research question (optional)",
        coerce=int,
        choices=[(0, "No linked question")],
        validators=[Optional()],
    )
    relevance_evidence = TextAreaField(
        "Evidence this source is relevant", validators=[Length(max=5000)]
    )
    evidence_url = StringField(
        "Evidence URL (required to mark researched)",
        validators=[Length(max=2048), validate_http_url],
    )
    competitor_reference = StringField(
        "Existing competitor reference (verified only)",
        validators=[Length(max=250)],
    )
    competitor_evidence_url = StringField(
        "Competitor evidence URL",
        validators=[Length(max=2048), validate_http_url],
    )
    suggested_angle = TextAreaField(
        "Suggested legitimate outreach / contribution angle",
        validators=[DataRequired(), Length(max=5000)],
    )
    priority = SelectField(
        "Priority",
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
        validators=[DataRequired()],
    )
    research_status = SelectField(
        "Research status",
        choices=[
            ("unresearched", "Unresearched"),
            ("in_progress", "In progress"),
            ("researched", "Researched"),
            ("dismissed", "Dismissed"),
        ],
        validators=[DataRequired()],
    )
    notes = TextAreaField("Notes", validators=[Length(max=5000)])
    submit = SubmitField("Save opportunity")

    def validate(self, extra_validators=None) -> bool:
        if not super().validate(extra_validators):
            return False
        valid = True
        if self.research_status.data == "researched" and not (
            self.relevance_evidence.data or ""
        ).strip():
            self.relevance_evidence.errors.append(
                "Record relevance evidence before marking this researched."
            )
            valid = False
        if self.competitor_reference.data and not self.competitor_evidence_url.data:
            self.competitor_evidence_url.errors.append(
                "A competitor reference requires a URL showing that reference."
            )
            valid = False
        return valid


class ReportRangeForm(FlaskForm):
    start_date = DateField("Start date", validators=[DataRequired()])
    end_date = DateField("End date", validators=[DataRequired()])
    submit = SubmitField("Run report")

    def validate_end_date(self, field) -> None:
        if self.start_date.data and field.data < self.start_date.data:
            raise ValidationError("End date must be on or after the start date.")
        if self.start_date.data and (field.data - self.start_date.data).days > 366:
            raise ValidationError("Report ranges are limited to 367 days.")


class GrowthTaskForm(FlaskForm):
    title = StringField("Task", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[Length(max=5000)])
    week_index = IntegerField(
        "Planning week", validators=[DataRequired()]
    )
    week_start = DateField("Week starting", validators=[DataRequired()])
    owner = StringField("Owner", validators=[Length(max=120)])
    tools = TextAreaField(
        "Tools (one per line)", validators=[Length(max=2000)]
    )
    budget_amount = FloatField("Budget amount", validators=[Optional()])
    budget_currency = StringField(
        "Budget currency (ISO 4217)", default="USD", validators=[DataRequired(), Length(min=3, max=3)]
    )
    target_metrics = TextAreaField(
        "Suggested target metrics (JSON object)",
        validators=[Optional(), Length(max=5000)],
    )
    target_is_suggested = BooleanField(
        "Targets are suggestions, not actual outcomes", default=True
    )
    risks = TextAreaField("Risks", validators=[Length(max=3000)])
    actual_outcome = TextAreaField(
        "Actual outcome (recorded only after work)", validators=[Length(max=5000)]
    )
    priority = SelectField(
        "Priority",
        choices=[("low", "Low"), ("medium", "Medium"), ("high", "High")],
        validators=[DataRequired()],
    )
    status = SelectField(
        "Status",
        choices=[
            ("backlog", "Backlog"),
            ("planned", "Planned"),
            ("in_progress", "In progress"),
            ("done", "Done"),
            ("blocked", "Blocked"),
        ],
        validators=[DataRequired()],
    )
    due_date = DateField("Due date", validators=[Optional()])
    submit = SubmitField("Save task")

    def validate_week_index(self, field) -> None:
        if field.data is not None and not 1 <= field.data <= 13:
            raise ValidationError("Choose a planning week between 1 and 13.")

    def validate_week_start(self, field) -> None:
        if field.data is not None and field.data.weekday() != 0:
            raise ValidationError("Week starting must be a Monday.")

    def validate_budget_amount(self, field) -> None:
        if field.data is not None and (
            not math.isfinite(field.data) or field.data < 0
        ):
            raise ValidationError("Enter a finite, non-negative budget.")

    def validate_target_metrics(self, field) -> None:
        if not (field.data or "").strip():
            return
        try:
            value = json.loads(field.data)
        except (json.JSONDecodeError, RecursionError):
            raise ValidationError("Enter a valid JSON object of suggested metrics.") from None
        if not isinstance(value, dict):
            raise ValidationError("Target metrics must be a JSON object.")

    def validate_budget_currency(self, field) -> None:
        if field.data and not re.fullmatch(r"[A-Za-z]{3}", field.data.strip()):
            raise ValidationError("Use a three-letter currency code such as USD.")
        if field.data:
            field.data = field.data.strip().upper()


class ContentBriefForm(FlaskForm):
    target_query = StringField(
        "Target query", validators=[DataRequired(), Length(max=500)]
    )
    search_intent = SelectField(
        "Search intent", choices=SEARCH_INTENTS, validators=[DataRequired()]
    )
    target_market = SelectField(
        "Target market", choices=MARKETS, validators=[DataRequired()]
    )
    audience = StringField(
        "Audience", validators=[DataRequired(), Length(max=250)]
    )
    supporting_questions = TextAreaField(
        "Supporting questions (one per line)", validators=[Length(max=5000)]
    )
    source_requirements = TextAreaField(
        "Source requirements", validators=[DataRequired(), Length(max=3000)]
    )
    internal_links = TextAreaField(
        "Internal-link suggestions (one HTTP/HTTPS URL per line)",
        validators=[Length(max=5000)],
    )
    schema_recommendation = StringField(
        "Schema recommendation", validators=[DataRequired(), Length(max=200)]
    )
    submit = SubmitField("Generate and save brief")

    def validate_internal_links(self, field) -> None:
        _validate_url_lines(field)


def _validate_url_lines(field) -> None:
    for line in (field.data or "").splitlines():
        value = line.strip()
        if value:
            try:
                parsed = urlsplit(value)
                _ = parsed.port
            except ValueError:
                parsed = None
            if (
                not parsed
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValidationError("Enter one absolute HTTP or HTTPS URL per line.")


QUESTION_CATEGORIES = [
    "AI content creation",
    "Editing",
    "Repurposing",
    "Captioning",
    "Publishing",
    "Content growth",
    "Comparisons",
    "Tool recommendations",
]

QUESTION_SOURCE_TYPES = [
    ("", "Select source type"),
    ("search_suggestions", "Search suggestions"),
    ("keyword_tool", "Keyword tool"),
    ("forum", "Forum"),
    ("community", "Community discussion"),
    ("people_also_ask", "People Also Ask"),
    ("other", "Other verified source"),
]

ENGINES = [
    ("Google Search", "Google Search"),
    ("Google AI Overviews", "Google AI Overviews"),
    ("ChatGPT", "ChatGPT"),
    ("Gemini", "Gemini"),
    ("Perplexity", "Perplexity"),
    ("Claude", "Claude"),
    ("Copilot", "Copilot"),
    ("Other", "Other (describe in notes)"),
]


class QuestionForm(FlaskForm):
    question_text = StringField(
        "Creator question", validators=[DataRequired(), Length(max=500)]
    )
    topic = SelectField(
        "Category",
        choices=[("", "Select category")] + [(value, value) for value in QUESTION_CATEGORIES],
        validators=[DataRequired()],
    )
    market = SelectField("Default market", choices=MARKETS, validators=[DataRequired()])
    language = StringField(
        "Language code", default="en", validators=[DataRequired(), Length(max=16)]
    )
    source_type = SelectField(
        "Source type", choices=QUESTION_SOURCE_TYPES, validators=[]
    )
    source_url = StringField(
        "Source URL", validators=[Length(max=2048), validate_http_url]
    )
    verification_status = SelectField(
        "Verification status",
        choices=[
            ("unverified", "Unverified"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
        ],
        default="unverified",
        validators=[DataRequired()],
    )
    submit = SubmitField("Save question")

    def validate_verification_status(self, field) -> None:
        if field.data == "verified" and not self.source_url.data:
            raise ValidationError("A source URL is required to verify a question.")


class MeasurementForm(FlaskForm):
    question_id = SelectField("Question", coerce=int, validators=[DataRequired()])
    market = SelectField("Market", choices=MARKETS, validators=[DataRequired()])
    location_setting = StringField(
        "Location setting",
        validators=[DataRequired(), Length(max=160)],
        description="Record the actual city/region and search location setting used.",
    )
    collected_at = StringField(
        "Collection date and time",
        validators=[DataRequired(), Length(max=40)],
        description="Use a local ISO date/time, for example 2026-10-30T14:30.",
    )
    query = StringField(
        "Observed search query",
        validators=[DataRequired(), Length(max=500)],
    )
    time_zone = StringField(
        "Time zone",
        default="UTC",
        validators=[DataRequired(), Length(max=64)],
        description="IANA time zone for the recorded local collection time.",
    )
    engine = SelectField(
        "Search engine or AI assistant", choices=ENGINES, validators=[DataRequired()]
    )
    engine_detail = StringField(
        "Other engine name (only for Other)",
        validators=[Length(max=80)],
    )
    collection_method = SelectField(
        "Collection method",
        choices=[
            ("manual", "Manual — observed by a person"),
            ("import", "Imported evidence"),
        ],
        validators=[DataRequired()],
    )
    raw_answer = TextAreaField(
        "Observed answer or result summary",
        validators=[Length(max=20000)],
    )
    mention_status = SelectField(
        "IncuBrix mention status",
        choices=[
            ("not_assessed", "Not assessed"),
            ("mentioned", "Mentioned"),
            ("not_mentioned", "Not mentioned"),
        ],
        default="not_assessed",
        validators=[DataRequired()],
    )
    mention_context = TextAreaField(
        "Mention context", validators=[Length(max=5000)]
    )
    sentiment = SelectField(
        "Sentiment (optional)",
        default="",
        choices=[
            ("", "Not assessed"),
            ("positive", "Positive"),
            ("neutral", "Neutral"),
            ("negative", "Negative"),
        ],
    )
    cited_urls = TextAreaField(
        "Cited URLs (one HTTP/HTTPS URL per line)", validators=[Length(max=10000)]
    )
    competitor_names = TextAreaField(
        "Competitor names observed (one per line)", validators=[Length(max=5000)]
    )
    competitor_evidence = TextAreaField(
        "Competitor evidence", validators=[Length(max=5000)]
    )
    google_rank = IntegerField(
        "Google organic rank (reliably observed only)",
        validators=[Optional()],
    )
    featured_snippet = SelectField(
        "Featured snippet / answer box",
        default="",
        choices=[("", "Not assessed"), ("yes", "Observed"), ("no", "Not observed")],
    )
    ai_overview = SelectField(
        "Google AI Overview",
        default="",
        choices=[("", "Not assessed"), ("yes", "Observed"), ("no", "Not observed")],
    )
    screenshot_reference = StringField(
        "Screenshot or evidence reference",
        validators=[Length(max=2048)],
        description="A workspace-relative file path or a real HTTP/HTTPS URL.",
    )
    visibility_score = FloatField(
        "Visibility score (optional, measured only)",
        validators=[Optional()],
    )
    collection_status = SelectField(
        "Collection status",
        choices=[
            ("complete", "Complete"),
            ("incomplete", "Incomplete"),
            ("failed", "Failed"),
        ],
        default="complete",
        validators=[DataRequired()],
    )
    error_status = TextAreaField(
        "Collection error (required for incomplete or failed)",
        validators=[Length(max=5000)],
    )
    notes = TextAreaField("Notes", validators=[Length(max=5000)])
    submit = SubmitField("Save immutable measurement")

    def validate_google_rank(self, field) -> None:
        if field.data is not None and field.data < 1:
            raise ValidationError("Google rank must be a positive whole number.")

    def validate_collected_at(self, field) -> None:
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(field.data)
            if "T" not in field.data or parsed.tzinfo is not None:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError("Enter a local date and time in YYYY-MM-DDTHH:MM format.")

    def validate_time_zone(self, field) -> None:
        try:
            ZoneInfo(field.data)
        except (ZoneInfoNotFoundError, TypeError):
            raise ValidationError("Enter a valid IANA time zone such as America/New_York.")

    def validate_visibility_score(self, field) -> None:
        if field.data is not None and not 0 <= field.data <= 100:
            raise ValidationError("Visibility score must be between 0 and 100.")

    def validate_screenshot_reference(self, field) -> None:
        if field.data and field.data.strip().startswith(("http://", "https://")):
            validate_http_url(self, field)

    def validate(self, extra_validators=None) -> bool:
        if not super().validate(extra_validators):
            return False
        if self.collection_status.data != "failed" and not (
            self.raw_answer.data or ""
        ).strip():
            self.raw_answer.errors.append("Enter the result observed, or mark collection failed.")
            return False
        if self.engine.data == "Other" and not (self.engine_detail.data or "").strip():
            self.engine_detail.errors.append("Enter the name of the engine you observed.")
            return False
        if self.collection_status.data in {"incomplete", "failed"} and not (
            self.error_status.data or ""
        ).strip():
            self.error_status.errors.append("Describe what was incomplete or failed.")
            return False
        return True
