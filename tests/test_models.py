import pytest
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Competitor, CompetitorMention, Measurement, Question, User


def test_question_has_multiple_independent_measurements(app):
    with app.app_context():
        user = User(
            email="models@example.com",
            full_name="Model Tester",
            contact_email="owner@example.com",
        )
        user.set_password("a-secure-test-password")
        question = Question(
            user=user,
            question_text="Where does this answer come from?",
            market="US",
        )
        question.measurements.extend(
            [
                Measurement(
                    market="US",
                    engine="Answer engine A",
                    query="source query A",
                    raw_answer="First raw answer.",
                    evidence={"quote": "first source"},
                    collection_method="manual",
                ),
                Measurement(
                    market="UK",
                    engine="Answer engine B",
                    query="source query B",
                    raw_answer="Second raw answer.",
                    evidence=["https://source.example/second"],
                    collection_method="import",
                ),
            ]
        )
        db.session.add(user)
        db.session.commit()

        loaded_question = db.session.get(Question, question.id)
        assert loaded_question is not None
        assert len(loaded_question.measurements) == 2
        assert {
            (item.market, item.engine, item.query, item.raw_answer)
            for item in loaded_question.measurements
        } == {
            ("US", "Answer engine A", "source query A", "First raw answer."),
            ("UK", "Answer engine B", "source query B", "Second raw answer."),
        }
        assert loaded_question.measurements[0].collected_at is not None


def test_password_is_hashed_and_email_is_normalized(app):
    with app.app_context():
        user = User(
            email="  Analyst@Example.COM ",
            full_name="Analyst",
            contact_email="OWNER@EXAMPLE.COM",
        )
        user.set_password("a-secure-test-password")

        assert user.email == "analyst@example.com"
        assert user.contact_email == "owner@example.com"
        assert user.password_hash != "a-secure-test-password"
        assert user.check_password("a-secure-test-password")
        assert not user.check_password("wrong-password")


@pytest.mark.parametrize(
    "values",
    [
        {"visibility_score": 101},
        {"visibility_score": -0.5},
        {"visibility_score": float("nan")},
        {"collection_method": "crawler"},
        {"raw_answer": " "},
        {"evidence": []},
    ],
)
def test_measurement_validation_rejects_invalid_values(app, values):
    with app.app_context(), pytest.raises(ValueError):
        measurement_values = {
            "user_id": 1,
            "question_id": 1,
            "market": "US",
            "engine": "Test engine",
            "query": "Test query",
            "raw_answer": "Recorded answer",
        }
        measurement_values.update(values)
        Measurement(**measurement_values)


def test_database_constraints_enforce_question_measurement_ownership(app):
    with app.app_context():
        owner = User(
            email="owner@example.com",
            full_name="Owner",
            contact_email="owner@example.com",
        )
        owner.set_password("a-secure-test-password")
        other = User(
            email="other@example.com",
            full_name="Other",
            contact_email="owner@example.com",
        )
        other.set_password("a-secure-test-password")
        db.session.add_all([owner, other])
        db.session.flush()
        question = Question(
            user_id=owner.id,
            question_text="Owner question",
            market="US",
        )
        db.session.add(question)
        db.session.flush()
        db.session.add(
            Measurement(
                user_id=other.id,
                question_id=question.id,
                market="US",
                engine="Test engine",
                query="cross-account query",
                raw_answer="This relationship is invalid.",
                collection_method="manual",
            )
        )

        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (
            Question,
            {"question_text": " ", "market": "US"},
        ),
        (
            Question,
            {"question_text": "A valid question?", "market": ""},
        ),
    ],
)
def test_question_rejects_empty_required_values(app, model, values):
    with app.app_context(), pytest.raises(ValueError):
        model(user_id=1, **values)


def test_content_and_citation_urls_must_use_http_or_https(app):
    from app.models import CitationTarget, ContentPage

    with app.app_context():
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            ContentPage(
                user_id=1,
                title="Unsafe URL",
                url="javascript:alert(1)",
            )
        with pytest.raises(ValueError, match="HTTP or HTTPS"):
            CitationTarget(
                user_id=1,
                domain="example.com",
                url="javascript:alert(1)",
            )
        for unsafe_url in (
            "https://user:password@example.com/path",
            "https://example.com:invalid/path",
        ):
            with pytest.raises(ValueError, match="HTTP or HTTPS"):
                ContentPage(user_id=1, title="Invalid URL", url=unsafe_url)


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1.0])
def test_growth_task_rejects_non_finite_or_negative_budgets(app, budget):
    from app.models import GrowthTask

    with app.app_context(), pytest.raises(ValueError):
        GrowthTask(user_id=1, title="Budget validation", budget_amount=budget)


def test_competitor_mention_links_competitor_to_its_measurement(app):
    with app.app_context():
        user = User(
            email="mentions@example.com",
            full_name="Mention Tester",
            contact_email="owner@example.com",
        )
        user.set_password("a-secure-test-password")
        question = Question(
            user=user,
            question_text="Which brands appear in the answer?",
            market="US",
        )
        measurement = Measurement(
            question=question,
            market="US",
            engine="Example answer engine",
            query="brands in the category",
            raw_answer="A recorded answer mentions a competitor.",
            collection_method="manual",
        )
        competitor = Competitor(user=user, name="Example Competitor")
        mention = CompetitorMention(
            user=user,
            competitor=competitor,
            measurement=measurement,
            rank=1,
            evidence_excerpt="A competitor was named first.",
        )
        db.session.add_all([user, question, measurement, competitor, mention])
        db.session.commit()

        assert measurement.competitor_mentions == [mention]
        assert competitor.mentions == [mention]
        assert mention.measurement.question is question
