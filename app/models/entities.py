import math
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from flask_login import UserMixin
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} is required.")
    if len(value) > limit:
        raise ValueError(f"{field} must be {limit} characters or fewer.")
    return value


def _http_url(value: str, field: str) -> str:
    value = _required_text(value, field, 2048)
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError(f"{field} must be an absolute HTTP or HTTPS URL.") from None
    if (
        any(ord(char) < 32 or ord(char) == 127 for char in value)
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{field} must be an absolute HTTP or HTTPS URL.")
    return value


class User(UserMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint("length(email) <= 254", name="ck_users_email_length"),
        Index("ix_users_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(db.String(254), nullable=False)
    password_hash: Mapped[str] = mapped_column(db.String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(db.String(120), nullable=False)
    contact_email: Mapped[str] = mapped_column(db.String(254), nullable=False)
    is_active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    questions: Mapped[list["Question"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", overlaps="measurements"
    )
    competitors: Mapped[list["Competitor"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    competitor_mentions: Mapped[list["CompetitorMention"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_runs: Mapped[list["AuditRun"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_pages: Mapped[list["AuditPage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_issues: Mapped[list["AuditIssue"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    content_pages: Mapped[list["ContentPage"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    citation_targets: Mapped[list["CitationTarget"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    weekly_reports: Mapped[list["WeeklyReport"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    growth_tasks: Mapped[list["GrowthTask"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    activity_logs: Mapped[list["ActivityLog"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @validates("email", "contact_email")
    def normalize_email(self, _key: str, value: str) -> str:
        value = _required_text(value, "Email", 254).lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value

    @validates("full_name")
    def validate_full_name(self, _key: str, value: str) -> str:
        return _required_text(value, "Full name", 120)

    def set_password(self, password: str) -> None:
        if not isinstance(password, str) or len(password) < 12:
            raise ValueError("Password must contain at least 12 characters.")
        if len(password) > 1024:
            raise ValueError("Password must be 1024 characters or fewer.")
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

class Question(db.Model):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("user_id", "question_text", name="uq_questions_user_text"),
        UniqueConstraint("user_id", "id", name="uq_questions_user_id"),
        Index("ix_questions_user_created_at", "user_id", "created_at"),
        Index("ix_questions_user_category_market", "user_id", "topic", "market"),
        CheckConstraint(
            "source_type IS NULL OR source_type IN "
            "('search_suggestions', 'keyword_tool', 'forum', 'community', 'people_also_ask', 'other')",
            name="ck_questions_source_type",
        ),
        CheckConstraint(
            "verification_status IN ('unverified', 'verified', 'rejected')",
            name="ck_questions_verification_status",
        ),
        CheckConstraint(
            "verification_status != 'verified' OR "
            "(source_url IS NOT NULL AND length(trim(source_url)) > 0)",
            name="ck_questions_verified_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_text: Mapped[str] = mapped_column(db.String(500), nullable=False)
    topic: Mapped[str | None] = mapped_column(db.String(120))
    market: Mapped[str] = mapped_column(db.String(100), nullable=False)
    language: Mapped[str] = mapped_column(db.String(16), nullable=False, default="en")
    source_url: Mapped[str | None] = mapped_column(db.String(2048))
    source_type: Mapped[str | None] = mapped_column(db.String(30))
    verification_status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="unverified"
    )
    is_active: Mapped[bool] = mapped_column(db.Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(
        back_populates="questions", foreign_keys=[user_id]
    )
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        foreign_keys="[Measurement.user_id, Measurement.question_id]",
        overlaps="measurements,user",
    )

    @validates("question_text")
    def validate_question_text(self, _key: str, value: str) -> str:
        return _required_text(value, "Question", 500)

    @validates("market")
    def validate_market(self, _key: str, value: str) -> str:
        return _required_text(value, "Market", 100)

    @validates("topic")
    def validate_topic(self, _key: str, value: str | None) -> str | None:
        if value is not None and value not in {
            "AI content creation",
            "Editing",
            "Repurposing",
            "Captioning",
            "Publishing",
            "Content growth",
            "Comparisons",
            "Tool recommendations",
        }:
            raise ValueError("Select a supported question category.")
        return value

    @validates("language")
    def validate_language(self, _key: str, value: str) -> str:
        return _required_text(value, "Language", 16).lower()

    @validates("source_url")
    def validate_source_url(self, _key: str, value: str | None) -> str | None:
        return _http_url(value, "Source URL") if value is not None and value.strip() else None

    @validates("source_type")
    def validate_source_type(self, _key: str, value: str | None) -> str | None:
        allowed = {
            "search_suggestions",
            "keyword_tool",
            "forum",
            "community",
            "people_also_ask",
            "other",
        }
        if value is not None and value not in allowed:
            raise ValueError("Select a supported source type.")
        return value

    @validates("verification_status")
    def validate_verification_status(self, _key: str, value: str) -> str:
        if value not in {"unverified", "verified", "rejected"}:
            raise ValueError("Verification status is invalid.")
        return value


class Measurement(db.Model):
    __tablename__ = "measurements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "question_id"],
            ["questions.user_id", "questions.id"],
            name="fk_measurements_question",
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "id", name="uq_measurements_user_id"),
        CheckConstraint(
            "visibility_score IS NULL OR "
            "(visibility_score >= 0 AND visibility_score <= 100)",
            name="ck_measurements_visibility_score",
        ),
        CheckConstraint(
            "collection_method IN ('manual', 'import', 'api')",
            name="ck_measurements_collection_method",
        ),
        Index("ix_measurements_user_collected_at", "user_id", "collected_at"),
        Index("ix_measurements_user_question", "user_id", "question_id"),
        CheckConstraint(
            "mention_status IS NULL OR mention_status IN "
            "('mentioned', 'not_mentioned', 'not_assessed')",
            name="ck_measurements_mention_status",
        ),
        CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive', 'neutral', 'negative')",
            name="ck_measurements_sentiment",
        ),
        CheckConstraint(
            "google_rank IS NULL OR google_rank > 0",
            name="ck_measurements_google_rank",
        ),
        CheckConstraint(
            "collection_status IN ('complete', 'incomplete', 'failed')",
            name="ck_measurements_collection_status",
        ),
        CheckConstraint(
            "collection_status = 'failed' OR length(trim(raw_answer)) > 0",
            name="ck_measurements_observed_answer",
        ),
        CheckConstraint(
            "collection_status = 'complete' OR "
            "(error_status IS NOT NULL AND length(trim(error_status)) > 0)",
            name="ck_measurements_collection_error",
        ),
        Index("ix_measurements_user_market_engine_date", "user_id", "market", "engine", "collected_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(db.Integer, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    market: Mapped[str] = mapped_column(db.String(100), nullable=False)
    time_zone: Mapped[str] = mapped_column(db.String(64), nullable=False, default="UTC")
    engine: Mapped[str] = mapped_column(db.String(80), nullable=False)
    query: Mapped[str] = mapped_column(db.String(500), nullable=False)
    raw_answer: Mapped[str] = mapped_column(db.Text, nullable=False)
    evidence: Mapped[Any | None] = mapped_column(db.JSON)
    collection_method: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="manual"
    )
    brand_mentioned: Mapped[bool | None] = mapped_column(db.Boolean)
    mention_status: Mapped[str | None] = mapped_column(db.String(20))
    mention_context: Mapped[str | None] = mapped_column(db.Text)
    sentiment: Mapped[str | None] = mapped_column(db.String(12))
    location_setting: Mapped[str | None] = mapped_column(db.String(160))
    cited_urls: Mapped[list[str] | None] = mapped_column(db.JSON)
    competitor_names: Mapped[list[str] | None] = mapped_column(db.JSON)
    google_rank: Mapped[int | None] = mapped_column(db.Integer)
    featured_snippet: Mapped[bool | None] = mapped_column(db.Boolean)
    ai_overview: Mapped[bool | None] = mapped_column(db.Boolean)
    screenshot_reference: Mapped[str | None] = mapped_column(db.String(2048))
    notes: Mapped[str | None] = mapped_column(db.Text)
    collection_status: Mapped[str] = mapped_column(
        db.String(16), nullable=False, default="complete"
    )
    error_status: Mapped[str | None] = mapped_column(db.Text)
    visibility_score: Mapped[float | None] = mapped_column(db.Float)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(
        back_populates="measurements", overlaps="measurements"
    )
    question: Mapped["Question"] = relationship(
        back_populates="measurements",
        foreign_keys=[user_id, question_id],
        overlaps="measurements,user",
    )
    competitor_mentions: Mapped[list["CompetitorMention"]] = relationship(
        back_populates="measurement",
        cascade="all, delete-orphan",
        foreign_keys="[CompetitorMention.user_id, CompetitorMention.measurement_id]",
        overlaps="competitor_mentions,measurement,user",
    )

    @validates("market")
    def validate_market(self, _key: str, value: str) -> str:
        return _required_text(value, "Market", 100)

    @validates("time_zone")
    def validate_time_zone(self, _key: str, value: str) -> str:
        return _required_text(value, "Time zone", 64)

    @validates("engine")
    def validate_engine(self, _key: str, value: str) -> str:
        return _required_text(value, "Engine", 80)

    @validates("query")
    def validate_query(self, _key: str, value: str) -> str:
        return _required_text(value, "Query", 500)

    @validates("raw_answer")
    def validate_raw_answer(self, _key: str, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("Raw answer must be text.")
        if not value.strip() and self.collection_status != "failed":
            raise ValueError("Raw answer is required unless collection failed.")
        return value

    @validates("evidence")
    def validate_evidence(self, _key: str, value: Any) -> Any:
        if value is not None and not isinstance(value, (dict, list, str)):
            raise ValueError("Evidence must be a JSON object, list, or text.")
        if value == {} or value == [] or value == "":
            raise ValueError("Evidence must not be empty.")
        return value

    @validates("collection_method")
    def validate_collection_method(self, _key: str, value: str) -> str:
        if value not in {"manual", "import", "api"}:
            raise ValueError("Collection method must be manual, import, or api.")
        return value

    @validates("visibility_score")
    def validate_visibility_score(self, _key: str, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("Visibility score must be between 0 and 100.")
        return value

    @validates("mention_status")
    def validate_mention_status(self, _key: str, value: str | None) -> str | None:
        allowed = {"mentioned", "not_mentioned", "not_assessed"}
        if value is not None and value not in allowed:
            raise ValueError("Mention status is invalid.")
        return value

    @validates("sentiment")
    def validate_sentiment(self, _key: str, value: str | None) -> str | None:
        if value is not None and value not in {"positive", "neutral", "negative"}:
            raise ValueError("Sentiment must be positive, neutral, or negative.")
        return value

    @validates("engine")
    def validate_engine(self, _key: str, value: str) -> str:
        return _required_text(value, "Engine", 80)

    @validates("cited_urls")
    def validate_cited_urls(self, _key: str, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if not isinstance(value, list):
                raise ValueError("Cited URLs must be a list.")
            return [_http_url(url, "Cited URL") for url in value]
        return value

    @validates("collection_status")
    def validate_collection_status(self, _key: str, value: str) -> str:
        if value not in {"complete", "incomplete", "failed"}:
            raise ValueError("Collection status is invalid.")
        return value

    @validates("google_rank")
    def validate_google_rank(self, _key: str, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("Google rank must be a positive whole number.")
        return value

    @validates("screenshot_reference")
    def validate_screenshot_reference(self, _key: str, value: str | None) -> str | None:
        if value is not None and value.strip().startswith(("http://", "https://")):
            return _http_url(value, "Screenshot reference")
        return _required_text(value, "Screenshot reference", 2048) if value else None

    @validates("location_setting")
    def validate_location_setting(self, _key: str, value: str | None) -> str | None:
        return _required_text(value, "Location setting", 160) if value else None

    @validates("competitor_names")
    def validate_competitor_names(self, _key: str, value: list[str] | None) -> list[str] | None:
        if value is not None:
            if not isinstance(value, list):
                raise ValueError("Competitor names must be a list.")
            return [_required_text(name, "Competitor name", 160) for name in value]
        return value


class Competitor(db.Model):
    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_competitors_user_name"),
        UniqueConstraint("user_id", "id", name="uq_competitors_user_id"),
        Index("ix_competitors_user_name", "user_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(db.String(160), nullable=False)
    domain: Mapped[str | None] = mapped_column(db.String(253))
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="competitors")
    mentions: Mapped[list["CompetitorMention"]] = relationship(
        back_populates="competitor",
        cascade="all, delete-orphan",
        overlaps="competitor_mentions",
    )

    @validates("name")
    def validate_name(self, _key: str, value: str) -> str:
        return _required_text(value, "Competitor name", 160)

    @validates("domain")
    def validate_domain(self, _key: str, value: str | None) -> str | None:
        if value is not None:
            normalized = _required_text(value, "Competitor domain", 253).lower()
            if "/" in normalized or " " in normalized:
                raise ValueError("Competitor domain must be a hostname.")
            return normalized
        return value


class CompetitorMention(db.Model):
    __tablename__ = "competitor_mentions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "competitor_id", "measurement_id", name="uq_competitor_mention"
        ),
        ForeignKeyConstraint(
            ["user_id", "competitor_id"],
            ["competitors.user_id", "competitors.id"],
            name="fk_competitor_mentions_competitor",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "measurement_id"],
            ["measurements.user_id", "measurements.id"],
            name="fk_competitor_mentions_measurement",
            ondelete="CASCADE",
        ),
        CheckConstraint("rank IS NULL OR rank > 0", name="ck_competitor_mentions_rank"),
        Index("ix_competitor_mentions_user_measurement", "user_id", "measurement_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    competitor_id: Mapped[int] = mapped_column(db.Integer, nullable=False)
    measurement_id: Mapped[int] = mapped_column(db.Integer, nullable=False)
    rank: Mapped[int | None] = mapped_column(db.Integer)
    evidence_excerpt: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(
        back_populates="competitor_mentions",
        overlaps="competitor_mentions,mentions",
    )
    competitor: Mapped["Competitor"] = relationship(
        back_populates="mentions",
        foreign_keys=[user_id, competitor_id],
        overlaps="competitor_mentions,user",
    )
    measurement: Mapped["Measurement"] = relationship(
        back_populates="competitor_mentions",
        foreign_keys=[user_id, measurement_id],
        overlaps="competitor,competitor_mentions,mentions,user",
    )


class AuditRun(db.Model):
    __tablename__ = "audit_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "id", name="uq_audit_runs_user_id"),
        CheckConstraint(
            "audit_type IN ('technical', 'content')", name="ck_audit_runs_type"
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed')",
            name="ck_audit_runs_status",
        ),
        Index("ix_audit_runs_user_started", "user_id", "started_at"),
        Index("ix_audit_runs_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    audit_type: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="technical"
    )
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="queued")
    started_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    root_url: Mapped[str | None] = mapped_column(db.String(2048))
    max_pages: Mapped[int | None] = mapped_column(db.Integer)
    max_depth: Mapped[int | None] = mapped_column(db.Integer)
    pages_crawled: Mapped[int] = mapped_column(
        db.Integer, nullable=False, default=0, server_default="0"
    )
    robots_status: Mapped[str | None] = mapped_column(db.String(80))
    sitemap_url: Mapped[str | None] = mapped_column(db.String(2048))
    sitemap_status: Mapped[str | None] = mapped_column(db.String(40))
    llms_status: Mapped[str | None] = mapped_column(db.String(80))
    https_available: Mapped[bool | None] = mapped_column(db.Boolean)
    error_summary: Mapped[str | None] = mapped_column(db.Text)
    recommendations: Mapped[list[dict[str, Any]] | None] = mapped_column(db.JSON)
    validation_checks: Mapped[list[dict[str, Any]] | None] = mapped_column(db.JSON)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="audit_runs")
    issues: Mapped[list["AuditIssue"]] = relationship(
        back_populates="audit_run",
        cascade="all, delete-orphan",
        foreign_keys="[AuditIssue.user_id, AuditIssue.audit_run_id]",
        overlaps="audit_issues,user",
    )
    pages: Mapped[list["AuditPage"]] = relationship(
        back_populates="audit_run",
        cascade="all, delete-orphan",
        foreign_keys="[AuditPage.user_id, AuditPage.audit_run_id]",
        overlaps="user,audit_pages",
    )

    @validates("audit_type")
    def validate_audit_type(self, _key: str, value: str) -> str:
        if value not in {"technical", "content"}:
            raise ValueError("Audit type must be technical or content.")
        return value

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        if value not in {"queued", "running", "completed", "failed"}:
            raise ValueError("Audit run status is invalid.")
        return value


class AuditPage(db.Model):
    __tablename__ = "audit_pages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "audit_run_id"],
            ["audit_runs.user_id", "audit_runs.id"],
            name="fk_audit_pages_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "audit_run_id", "url", name="uq_audit_pages_run_url"),
        CheckConstraint(
            "crawl_status IN ('fetched', 'blocked', 'failed', 'skipped')",
            name="ck_audit_pages_status",
        ),
        Index("ix_audit_pages_user_run", "user_id", "audit_run_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    audit_run_id: Mapped[int] = mapped_column(db.Integer, nullable=False)
    url: Mapped[str] = mapped_column(db.String(2048), nullable=False)
    final_url: Mapped[str | None] = mapped_column(db.String(2048))
    depth: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
    crawl_status: Mapped[str] = mapped_column(
        db.String(16), nullable=False, default="fetched"
    )
    status_code: Mapped[int | None] = mapped_column(db.Integer)
    content_type: Mapped[str | None] = mapped_column(db.String(160))
    title: Mapped[str | None] = mapped_column(db.String(1000))
    title_length: Mapped[int | None] = mapped_column(db.Integer)
    meta_description: Mapped[str | None] = mapped_column(db.Text)
    canonical_url: Mapped[str | None] = mapped_column(db.String(2048))
    h1_count: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
    internal_links: Mapped[list[str] | None] = mapped_column(db.JSON)
    broken_internal_links: Mapped[list[dict[str, Any]] | None] = mapped_column(db.JSON)
    response_bytes: Mapped[int | None] = mapped_column(db.Integer)
    response_ms: Mapped[int | None] = mapped_column(db.Integer)
    redirect_chain: Mapped[list[str] | None] = mapped_column(db.JSON)
    has_viewport: Mapped[bool | None] = mapped_column(db.Boolean)
    jsonld_present: Mapped[bool | None] = mapped_column(db.Boolean)
    jsonld_valid: Mapped[bool | None] = mapped_column(db.Boolean)
    images_total: Mapped[int | None] = mapped_column(db.Integer)
    images_missing_alt: Mapped[int | None] = mapped_column(db.Integer)
    error_summary: Mapped[str | None] = mapped_column(db.Text)
    fetched_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="audit_pages")
    audit_run: Mapped["AuditRun"] = relationship(
        back_populates="pages",
        foreign_keys=[user_id, audit_run_id],
        overlaps="user,audit_pages",
    )


class AuditIssue(db.Model):
    __tablename__ = "audit_issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "audit_run_id"],
            ["audit_runs.user_id", "audit_runs.id"],
            name="fk_audit_issues_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')",
            name="ck_audit_issues_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved', 'ignored')",
            name="ck_audit_issues_status",
        ),
        Index("ix_audit_issues_user_status_severity", "user_id", "status", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    audit_run_id: Mapped[int] = mapped_column(db.Integer, nullable=False)
    title: Mapped[str] = mapped_column(db.String(200), nullable=False)
    description: Mapped[str] = mapped_column(db.Text, nullable=False)
    severity: Mapped[str] = mapped_column(db.String(20), nullable=False)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="open")
    page_url: Mapped[str | None] = mapped_column(db.String(2048))
    category: Mapped[str] = mapped_column(
        db.String(40), nullable=False, default="other", server_default="other"
    )
    evidence: Mapped[str | None] = mapped_column(db.Text)
    recommendation: Mapped[str | None] = mapped_column(db.Text)
    code_example: Mapped[str | None] = mapped_column(db.Text)
    validation_method: Mapped[str | None] = mapped_column(db.Text)
    validation_status: Mapped[str] = mapped_column(
        db.String(24),
        nullable=False,
        default="not_validated",
        server_default="not_validated",
    )
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(
        back_populates="audit_issues", overlaps="issues"
    )
    audit_run: Mapped["AuditRun"] = relationship(
        back_populates="issues",
        foreign_keys=[user_id, audit_run_id],
        overlaps="audit_issues,user",
    )

    @validates("title")
    def validate_title(self, _key: str, value: str) -> str:
        return _required_text(value, "Issue title", 200)

    @validates("description")
    def validate_description(self, _key: str, value: str) -> str:
        return _required_text(value, "Issue description", 10000)

    @validates("severity")
    def validate_severity(self, _key: str, value: str) -> str:
        if value not in {"critical", "high", "medium", "low"}:
            raise ValueError("Severity must be critical, high, medium, or low.")
        return value

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        if value not in {"open", "in_progress", "resolved", "ignored"}:
            raise ValueError("Issue status is invalid.")
        return value

    @validates("validation_status")
    def validate_validation_status(self, _key: str, value: str) -> str:
        if value not in {"not_validated", "locally_validated", "externally_validated"}:
            raise ValueError("Validation status is invalid.")
        return value


class ContentPage(db.Model):
    __tablename__ = "content_pages"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_content_pages_user_url"),
        CheckConstraint(
            "status IN ('draft', 'published', 'needs_update', 'archived')",
            name="ck_content_pages_status",
        ),
        CheckConstraint(
            "content_type IN ('how_to', 'comparison', 'faq', 'glossary', 'brief', 'article')",
            name="ck_content_pages_content_type",
        ),
        Index("ix_content_pages_user_status", "user_id", "status"),
        Index("ix_content_pages_user_question", "user_id", "research_question_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(db.String(250), nullable=False)
    url: Mapped[str] = mapped_column(db.String(2048), nullable=False)
    status: Mapped[str] = mapped_column(db.String(24), nullable=False, default="draft")
    content_type: Mapped[str] = mapped_column(
        db.String(40), nullable=False, default="article"
    )
    seo_title: Mapped[str | None] = mapped_column(db.String(250))
    meta_description: Mapped[str | None] = mapped_column(db.String(320))
    slug: Mapped[str | None] = mapped_column(db.String(250))
    target_question: Mapped[str | None] = mapped_column(db.String(500))
    primary_keyword: Mapped[str | None] = mapped_column(db.String(250))
    search_intent: Mapped[str | None] = mapped_column(db.String(40))
    target_market: Mapped[str | None] = mapped_column(db.String(100))
    intended_audience: Mapped[str | None] = mapped_column(db.String(250))
    body_markdown: Mapped[str | None] = mapped_column(db.Text)
    supporting_sources: Mapped[list[str] | None] = mapped_column(db.JSON)
    internal_links: Mapped[list[str] | None] = mapped_column(db.JSON)
    schema_markup: Mapped[dict[str, Any] | None] = mapped_column(db.JSON)
    quality_checklist: Mapped[list[dict[str, Any]] | None] = mapped_column(db.JSON)
    claims_to_verify: Mapped[list[str] | None] = mapped_column(db.JSON)
    generation_mode: Mapped[str] = mapped_column(
        db.String(24), nullable=False, default="template", server_default="template"
    )
    research_question_id: Mapped[int | None] = mapped_column(
        db.Integer, db.ForeignKey("questions.id", ondelete="SET NULL")
    )
    audit_issue_id: Mapped[int | None] = mapped_column(
        db.Integer, db.ForeignKey("audit_issues.id", ondelete="SET NULL")
    )
    archived_at: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    last_reviewed_at: Mapped[datetime | None] = mapped_column(db.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="content_pages")
    research_question: Mapped["Question | None"] = relationship()
    audit_issue: Mapped["AuditIssue | None"] = relationship()

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        if value not in {"draft", "published", "needs_update", "archived"}:
            raise ValueError("Content status is invalid.")
        return value

    @validates("content_type")
    def validate_content_type(self, _key: str, value: str) -> str:
        if value not in {"how_to", "comparison", "faq", "glossary", "brief", "article"}:
            raise ValueError("Content format is invalid.")
        return value

    @validates("generation_mode")
    def validate_generation_mode(self, _key: str, value: str) -> str:
        if value not in {"template", "llm"}:
            raise ValueError("Draft generation mode is invalid.")
        return value

    @validates("seo_title", "meta_description", "slug", "primary_keyword",
               "search_intent", "target_market", "intended_audience")
    def validate_optional_metadata(self, key: str, value: str | None) -> str | None:
        limits = {
            "seo_title": 250,
            "meta_description": 320,
            "slug": 250,
            "primary_keyword": 250,
            "search_intent": 40,
            "target_market": 100,
            "intended_audience": 250,
        }
        return (
            _required_text(value, key.replace("_", " ").title(), limits[key])
            if value is not None
            else None
        )

    @validates("title", "url")
    def validate_required_fields(self, key: str, value: str) -> str:
        if key == "title":
            return _required_text(value, "Page title", 250)
        return _http_url(value, "Page URL")


class CitationTarget(db.Model):
    __tablename__ = "citation_targets"
    __table_args__ = (
        UniqueConstraint("user_id", "url", name="uq_citation_targets_user_url"),
        CheckConstraint(
            "status IN ('opportunity', 'monitoring', 'cited', 'dismissed')",
            name="ck_citation_targets_status",
        ),
        CheckConstraint(
            "opportunity_score IS NULL OR "
            "(opportunity_score >= 0 AND opportunity_score <= 100)",
            name="ck_citation_targets_score",
        ),
        Index("ix_citation_targets_user_status", "user_id", "status"),
        Index("ix_citation_targets_user_research_status", "user_id", "research_status"),
        CheckConstraint(
            "source_type IN ('article', 'directory', 'comparison', 'review', 'community')",
            name="ck_citation_targets_source_type",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_citation_targets_priority",
        ),
        CheckConstraint(
            "research_status IN ('unresearched', 'in_progress', 'researched', 'dismissed')",
            name="ck_citation_targets_research_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(db.String(253), nullable=False)
    url: Mapped[str] = mapped_column(db.String(2048), nullable=False)
    title: Mapped[str | None] = mapped_column(db.String(250))
    status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="opportunity"
    )
    opportunity_score: Mapped[float | None] = mapped_column(db.Float)
    source_name: Mapped[str | None] = mapped_column(db.String(250))
    source_type: Mapped[str] = mapped_column(
        db.String(24), nullable=False, default="article", server_default="article"
    )
    target_question: Mapped[str | None] = mapped_column(db.String(500))
    target_question_id: Mapped[int | None] = mapped_column(
        db.Integer, db.ForeignKey("questions.id", ondelete="SET NULL")
    )
    relevance_evidence: Mapped[str | None] = mapped_column(db.Text)
    evidence_url: Mapped[str | None] = mapped_column(db.String(2048))
    competitor_reference: Mapped[str | None] = mapped_column(db.String(250))
    competitor_evidence_url: Mapped[str | None] = mapped_column(db.String(2048))
    suggested_angle: Mapped[str | None] = mapped_column(db.Text)
    priority: Mapped[str] = mapped_column(
        db.String(12), nullable=False, default="medium", server_default="medium"
    )
    research_status: Mapped[str] = mapped_column(
        db.String(20), nullable=False, default="unresearched", server_default="unresearched"
    )
    notes: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="citation_targets")
    question: Mapped["Question | None"] = relationship()

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        if value not in {"opportunity", "monitoring", "cited", "dismissed"}:
            raise ValueError("Citation target status is invalid.")
        return value

    @validates("opportunity_score")
    def validate_opportunity_score(self, _key: str, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("Opportunity score must be between 0 and 100.")
        return value

    @validates("source_type")
    def validate_source_type(self, _key: str, value: str) -> str:
        if value not in {"article", "directory", "comparison", "review", "community"}:
            raise ValueError("Citation source type is invalid.")
        return value

    @validates("priority")
    def validate_priority(self, _key: str, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("Citation priority must be low, medium, or high.")
        return value

    @validates("research_status")
    def validate_research_status(self, _key: str, value: str) -> str:
        if value not in {"unresearched", "in_progress", "researched", "dismissed"}:
            raise ValueError("Citation research status is invalid.")
        return value

    @validates("source_name")
    def validate_source_name(self, _key: str, value: str | None) -> str | None:
        return _required_text(value, "Source name", 250) if value is not None else None

    @validates("target_question")
    def validate_target_question(self, _key: str, value: str | None) -> str | None:
        return _required_text(value, "Target question", 500) if value is not None else None

    @validates("evidence_url", "competitor_evidence_url")
    def validate_evidence_url(self, key: str, value: str | None) -> str | None:
        return _http_url(value, key.replace("_", " ").title()) if value else None

    @validates("competitor_reference")
    def validate_competitor_reference(self, _key: str, value: str | None) -> str | None:
        return (
            _required_text(value, "Competitor reference", 250)
            if value is not None
            else None
        )

    @validates("domain", "url")
    def validate_required_location(self, key: str, value: str) -> str:
        if key == "domain":
            normalized = _required_text(value, "Domain", 253).lower()
            if "/" in normalized or " " in normalized:
                raise ValueError("Domain must be a hostname.")
            return normalized
        return _http_url(value, "URL")

    @validates("title")
    def validate_title(self, _key: str, value: str | None) -> str | None:
        return _required_text(value, "Citation title", 250) if value is not None else None


class WeeklyReport(db.Model):
    __tablename__ = "weekly_reports"
    __table_args__ = (
        UniqueConstraint("user_id", "report_key", name="uq_weekly_reports_user_key"),
        Index("ix_weekly_reports_user_week", "user_id", "week_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    week_start: Mapped[date] = mapped_column(db.Date, nullable=False)
    period_end: Mapped[date | None] = mapped_column(db.Date)
    report_key: Mapped[str | None] = mapped_column(db.String(80))
    summary: Mapped[str] = mapped_column(db.Text, nullable=False)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(db.JSON)
    execution_status: Mapped[str] = mapped_column(
        db.String(16), nullable=False, default="completed", server_default="completed"
    )
    execution_duration_ms: Mapped[int | None] = mapped_column(db.Integer)
    error_message: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="weekly_reports")

    @validates("execution_status")
    def validate_execution_status(self, _key: str, value: str) -> str:
        if value not in {"running", "completed", "failed"}:
            raise ValueError("Report execution status is invalid.")
        return value


class GrowthTask(db.Model):
    __tablename__ = "growth_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('backlog', 'planned', 'in_progress', 'done', 'blocked')",
            name="ck_growth_tasks_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')", name="ck_growth_tasks_priority"
        ),
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="ck_growth_tasks_budget_nonnegative",
        ),
        CheckConstraint(
            "week_index IS NULL OR (week_index >= 1 AND week_index <= 13)",
            name="ck_growth_tasks_week_index",
        ),
        Index("ix_growth_tasks_user_due_status", "user_id", "due_date", "status"),
        Index("ix_growth_tasks_user_week_start", "user_id", "week_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(db.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(db.Text)
    status: Mapped[str] = mapped_column(db.String(20), nullable=False, default="planned")
    priority: Mapped[str] = mapped_column(db.String(12), nullable=False, default="medium")
    due_date: Mapped[date | None] = mapped_column(db.Date)
    week_index: Mapped[int | None] = mapped_column(db.Integer)
    week_start: Mapped[date | None] = mapped_column(db.Date)
    owner: Mapped[str | None] = mapped_column(db.String(120))
    tools: Mapped[list[str] | None] = mapped_column(db.JSON)
    budget_amount: Mapped[float | None] = mapped_column(db.Float)
    budget_currency: Mapped[str] = mapped_column(
        db.String(3), nullable=False, default="USD", server_default="USD"
    )
    target_metrics: Mapped[dict[str, Any] | None] = mapped_column(db.JSON)
    target_is_suggested: Mapped[bool] = mapped_column(
        db.Boolean, nullable=False, default=True, server_default=db.true()
    )
    risks: Mapped[str | None] = mapped_column(db.Text)
    actual_outcome: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="growth_tasks")

    @validates("status")
    def validate_status(self, _key: str, value: str) -> str:
        if value not in {"backlog", "planned", "in_progress", "done", "blocked"}:
            raise ValueError("Growth task status is invalid.")
        return value

    @validates("priority")
    def validate_priority(self, _key: str, value: str) -> str:
        if value not in {"low", "medium", "high"}:
            raise ValueError("Growth task priority is invalid.")
        return value

    @validates("title")
    def validate_title(self, _key: str, value: str) -> str:
        return _required_text(value, "Task title", 200)

    @validates("owner")
    def validate_owner(self, _key: str, value: str | None) -> str | None:
        return _required_text(value, "Task owner", 120) if value else None

    @validates("budget_currency")
    def validate_budget_currency(self, _key: str, value: str) -> str:
        normalized = _required_text(value, "Budget currency", 3).upper()
        if not re.fullmatch(r"[A-Z]{3}", normalized):
            raise ValueError("Budget currency must be a three-letter code.")
        return normalized

    @validates("budget_amount")
    def validate_budget_amount(self, _key: str, value: float | None) -> float | None:
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("Budget amount must be finite and non-negative.")
        return value

    @validates("week_index")
    def validate_week_index(self, _key: str, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 13:
            raise ValueError("Planning week must be between 1 and 13.")
        return value


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"
    __table_args__ = (Index("ix_activity_logs_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(db.String(120), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(db.String(80))
    entity_id: Mapped[int | None] = mapped_column(db.Integer)
    details: Mapped[dict[str, Any] | None] = mapped_column(db.JSON)
    created_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False, default=utc_now
    )

    user: Mapped["User"] = relationship(back_populates="activity_logs")

    @validates("action")
    def validate_action(self, _key: str, value: str) -> str:
        return _required_text(value, "Activity action", 120)


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_login_attempts_count_nonnegative"),
        Index("ix_login_attempts_window_started", "window_started_at"),
    )

    bucket_key: Mapped[str] = mapped_column(db.String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        db.DateTime(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(db.Integer, nullable=False, default=0)
