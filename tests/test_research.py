import csv
import io
import re
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Measurement, Question, User
from app.services.questions import QuestionImportError, parse_questions_csv
from app.services.visibility import calculate_visibility
from app.services.measurement_adapters import IntegrationUnavailable, get_adapter


def _create_user(email="research@example.com"):
    user = User(
        email=email,
        full_name="Research Analyst",
        contact_email="owner@example.com",
    )
    user.set_password("a-secure-test-password")
    db.session.add(user)
    db.session.commit()
    return user


def _csrf(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def _sign_in(client, email="research@example.com"):
    token = _csrf(client, "/auth/login")
    return client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": email,
            "password": "a-secure-test-password",
        },
        follow_redirects=True,
    )


def _question(user_id, text="How do creators plan a content calendar?"):
    question = Question(user_id=user_id, question_text=text, market="US")
    db.session.add(question)
    db.session.commit()
    return question


def test_research_routes_require_authentication(client):
    assert client.get("/research/").status_code == 302
    assert client.get("/research/measurements").status_code == 302
    assert client.get("/research/visibility.csv").status_code == 302
    token = _csrf(client, "/auth/login")
    response = client.post(
        "/research/measurements",
        data={"csrf_token": token},
    )
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_assistant_profile_is_explicitly_manual_without_authorized_api():
    adapter = get_adapter("ChatGPT")

    assert adapter.configured is False
    with pytest.raises(IntegrationUnavailable, match="No authorized API integration"):
        adapter.collect("a real user query", "US", "New York, NY")


def test_question_create_rejects_unreferenced_verification_and_duplicates(
    app, client
):
    with app.app_context():
        _create_user()
    _sign_in(client)

    token = _csrf(client, "/research/questions/new")
    invalid = client.post(
        "/research/questions/new",
        data={
            "csrf_token": token,
            "question_text": "How do creators plan a content calendar?",
            "topic": "Publishing",
            "market": "US",
            "language": "en",
            "source_type": "forum",
            "source_url": "",
            "verification_status": "verified",
            "submit": "Save question",
        },
    )
    assert invalid.status_code == 200
    assert b"A source URL is required" in invalid.data

    token = _csrf(client, "/research/questions/new")
    created = client.post(
        "/research/questions/new",
        data={
            "csrf_token": token,
            "question_text": "How do creators plan a content calendar?",
            "topic": "Publishing",
            "market": "US",
            "language": "en",
            "source_type": "forum",
            "source_url": "https://community.example/questions/1",
            "verification_status": "verified",
            "submit": "Save question",
        },
    )
    assert created.status_code == 302

    token = _csrf(client, "/research/questions/new")
    duplicate = client.post(
        "/research/questions/new",
        data={
            "csrf_token": token,
            "question_text": "  HOW do creators plan a content   calendar? ",
            "topic": "Publishing",
            "market": "US",
            "language": "en",
            "source_type": "forum",
            "source_url": "",
            "verification_status": "unverified",
            "submit": "Save question",
        },
        follow_redirects=True,
    )
    assert b"duplicates the existing question" in duplicate.data
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Question.id))) == 1


def test_question_source_and_measurements_are_tenant_scoped(app, client):
    with app.app_context():
        owner = _create_user()
        owned = _question(owner.id)
        other = _create_user("other-research@example.com")
        private_question = _question(other.id, "Private question from another account?")
        owned_id, private_id = owned.id, private_question.id

    _sign_in(client)
    assert client.get(f"/research/questions/{private_id}").status_code == 404
    exported = client.get("/research/questions/export.csv")
    assert b"How do creators plan a content calendar?" in exported.data
    assert b"Private question" not in exported.data

    token = _csrf(client, "/research/questions/new")
    cross_account_measurement = client.post(
        "/research/measurements",
        data={"csrf_token": token, "question_id": private_id},
    )
    assert cross_account_measurement.status_code == 400
    with app.app_context():
        assert db.session.get(Question, owned_id) is not None


def test_measurement_form_preserves_observation_and_converts_local_time(
    app, client
):
    with app.app_context():
        user = _create_user()
        _question(user.id)
    _sign_in(client)

    form_path = "/research/measurements/new"
    token = _csrf(client, form_path)
    response = client.post(
        "/research/measurements",
        data={
            "csrf_token": token,
            "question_id": "1",
            "market": "CA",
            "location_setting": "Toronto, Ontario, Canada",
            "collected_at": "2026-10-25T10:00",
            "time_zone": "America/Toronto",
            "engine": "Gemini",
            "engine_detail": "",
            "collection_method": "manual",
            "collection_status": "complete",
            "error_status": "",
            "query": "best AI content planning tools",
            "raw_answer": "Observed answer recorded by the analyst.",
            "mention_status": "mentioned",
            "mention_context": "Brand named in the second paragraph.",
            "sentiment": "neutral",
            "cited_urls": "https://brand.example/source",
            "competitor_names": "Observed Competitor",
            "competitor_evidence": "Named in the answer.",
            "google_rank": "",
            "featured_snippet": "",
            "ai_overview": "",
            "screenshot_reference": "evidence/gemini-toronto.png",
            "visibility_score": "45",
            "notes": "Manual observation.",
            "submit": "Save immutable measurement",
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    assert "/research/measurements/" in response.headers["Location"]

    with app.app_context():
        measurement = db.session.scalar(db.select(Measurement))
        assert measurement is not None
        assert measurement.collected_at.replace(tzinfo=timezone.utc) == datetime(
            2026, 10, 25, 14, 0, tzinfo=timezone.utc
        )
        assert measurement.market == "CA"
        assert measurement.engine == "Gemini"
        assert measurement.query == "best AI content planning tools"
        assert measurement.raw_answer == "Observed answer recorded by the analyst."
        assert measurement.mention_status == "mentioned"
        assert measurement.cited_urls == ["https://brand.example/source"]
        assert measurement.competitor_names == ["Observed Competitor"]
        assert measurement.collection_method == "manual"

    detail = client.get(response.headers["Location"])
    assert b"Manual" in detail.data
    export = client.get("/research/visibility.csv")
    rows = list(csv.DictReader(io.StringIO(export.get_data(as_text=True))))
    assert rows[0]["query"] == "best AI content planning tools"
    assert rows[0]["market"] == "CA"
    assert rows[0]["verification_status"] == "unverified"


def test_visibility_metrics_exclude_unassessed_and_failed_observations(app):
    with app.app_context():
        user = _create_user()
        question = _question(user.id)
        later = datetime(2026, 10, 25, tzinfo=timezone.utc)
        db.session.add_all(
            [
                Measurement(
                    user_id=user.id,
                    question_id=question.id,
                    collected_at=later - timedelta(days=7),
                    market="US",
                    engine="ChatGPT",
                    query="original query",
                    raw_answer="Observed answer mentions IncuBrix.",
                    collection_method="manual",
                    mention_status="mentioned",
                    cited_urls=["https://brand.example/citation"],
                    evidence={"reference": "capture-1.png"},
                ),
                Measurement(
                    user_id=user.id,
                    question_id=question.id,
                    collected_at=later,
                    market="US",
                    engine="ChatGPT",
                    query="repeat query",
                    raw_answer="Repeat check was not assessed for a mention.",
                    collection_method="manual",
                    mention_status="not_assessed",
                ),
                Measurement(
                    user_id=user.id,
                    question_id=question.id,
                    collected_at=later + timedelta(days=1),
                    market="US",
                    engine="ChatGPT",
                    query="failed query",
                    collection_status="failed",
                    raw_answer="",
                    collection_method="manual",
                    mention_status="not_assessed",
                    error_status="The authorized interface was unavailable.",
                ),
            ]
        )
        db.session.commit()

        metrics = calculate_visibility(user.id)

    assert metrics["observation_count"] == 3
    assert metrics["assessed_sample"] == 1
    assert metrics["mentioned_count"] == 1
    assert metrics["mention_rate"] == 100
    assert metrics["citation_rate"] == 100
    assert metrics["evidence_questions"] == 1
    assert metrics["breakdown"][0]["sample_size"] == 3
    assert metrics["breakdown"][0]["assessed_sample"] == 1


def test_failed_manual_check_keeps_result_empty_and_is_not_a_negative_mention(
    app, client
):
    with app.app_context():
        user = _create_user()
        _question(user.id)
    _sign_in(client)
    token = _csrf(client, "/research/measurements/new")

    response = client.post(
        "/research/measurements",
        data={
            "csrf_token": token,
            "question_id": "1",
            "market": "US",
            "location_setting": "New York, NY, United States",
            "collected_at": "2026-10-25T10:00",
            "time_zone": "America/New_York",
            "engine": "ChatGPT",
            "collection_method": "manual",
            "collection_status": "failed",
            "error_status": "The assistant was unavailable.",
            "query": "creator tools",
            "raw_answer": "",
            "mention_status": "not_assessed",
            "cited_urls": "",
            "competitor_names": "",
            "google_rank": "",
            "featured_snippet": "",
            "ai_overview": "",
            "screenshot_reference": "",
            "visibility_score": "",
            "notes": "",
        },
    )
    assert response.status_code == 302, response.get_data(as_text=True)
    with app.app_context():
        measurement = db.session.scalar(db.select(Measurement))
        assert measurement is not None
        assert measurement.collection_status == "failed"
        assert measurement.raw_answer == ""
        metrics = calculate_visibility(measurement.user_id)
        assert metrics["observation_count"] == 1
        assert metrics["assessed_sample"] == 0
        assert metrics["mention_rate"] is None


def test_csv_import_is_atomic_on_duplicates_and_extra_columns(app):
    with app.app_context():
        user = _create_user()
        _question(user.id, "Existing question?")
        headers = (
            "question,topic,source_url,source_type,market,language,"
            "verification_status"
        )
        csv_content = (
            f"{headers}\n"
            '"A real sourced question?","Editing","https://source.example/a",'
            '"forum","US","en","unverified"\n'
            '" Existing   question? ","Editing","","forum","US","en","unverified"\n'
        )
        with pytest.raises(QuestionImportError, match="duplicate question"):
            parse_questions_csv(csv_content, user.id)

        malformed = (
            f"{headers}\n"
            '"A question?","Editing","","forum","US","en","unverified","extra"\n'
        )
        with pytest.raises(QuestionImportError, match="extra columns"):
            parse_questions_csv(malformed, user.id)
        assert db.session.scalar(
            db.select(db.func.count(Question.id)).where(Question.user_id == user.id)
        ) == 1


def test_verified_question_constraint_rejects_missing_source(app):
    with app.app_context():
        user = _create_user()
        question = Question(
            user_id=user.id,
            question_text="A question incorrectly marked verified?",
            market="US",
            verification_status="verified",
        )
        db.session.add(question)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
