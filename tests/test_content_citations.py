import json
import re
from urllib.error import HTTPError

import pytest

from app.extensions import db
from app.models import AuditIssue, AuditRun, CitationTarget, ContentPage, Question, User
from app.services.content_drafting import (
    ContentGenerationError,
    generate_content_draft,
)


def _create_user(email="writer@example.com"):
    user = User(
        email=email,
        full_name="Content Writer",
        contact_email="owner@example.com",
    )
    user.set_password("a-secure-test-password")
    db.session.add(user)
    db.session.commit()
    return user


def _token(client, path):
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return match.group(1).decode()


def _sign_in(client, email="writer@example.com"):
    token = _token(client, "/auth/login")
    return client.post(
        "/auth/login",
        data={
            "csrf_token": token,
            "email": email,
            "password": "a-secure-test-password",
        },
        follow_redirects=True,
    )


def _draft_form(**updates):
    values = {
        "target_question": "How can creators plan a consistent publishing workflow?",
        "primary_keyword": "publishing workflow",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Independent content creators",
        "supporting_sources": "https://docs.example.org/workflow",
        "internal_links": "https://brand.example/resources",
        "content_type": "faq",
        "generation_mode": "template",
        "research_question_id": "0",
        "audit_issue_id": "0",
        "status": "draft",
    }
    values.update(updates)
    return values


def _citation_form(**updates):
    values = {
        "source_name": "Creator resources directory",
        "url": "https://directory.example.org/creators",
        "source_type": "directory",
        "target_question": "Which publishing tools support a small creator team?",
        "target_question_id": "0",
        "relevance_evidence": "",
        "evidence_url": "",
        "competitor_reference": "",
        "competitor_evidence_url": "",
        "suggested_angle": "Offer a concise, sourced workflow contribution for the directory audience.",
        "priority": "medium",
        "research_status": "unresearched",
        "notes": "",
    }
    values.update(updates)
    return values


def test_template_content_create_edit_preview_duplicate_archive_and_exports(client, app):
    with app.app_context():
        _create_user()
    assert _sign_in(client).status_code == 200

    token = _token(client, "/content/new")
    created = client.post(
        "/content/new",
        data={**_draft_form(), "csrf_token": token},
        follow_redirects=True,
    )
    assert created.status_code == 200
    assert b"Template-based draft" in created.data
    assert b"FAQPage" in created.data
    assert b"Verify every product" in created.data
    with app.app_context():
        page = ContentPage.query.one()
        page_id = page.id
        assert page.generation_mode == "template"
        assert page.status == "draft"
        assert page.schema_markup["@type"] == "FAQPage"
        assert "https://docs.example.org/workflow" in page.body_markdown
        assert page.quality_checklist

    edit_token = _token(client, f"/content/{page_id}/edit")
    edited = client.post(
        f"/content/{page_id}/edit",
        data={
            **_draft_form(
                target_question="How should a team review its publishing workflow?",
                supporting_sources="https://docs.example.org/updated",
                internal_links="",
                generation_mode="llm",
                status="needs_update",
            ),
            "csrf_token": edit_token,
            "seo_title": "Publishing workflow review",
            "meta_description": "A checklist for reviewing a creator publishing workflow.",
            "slug": "publishing-workflow-review",
            "body_markdown": "# Publishing workflow review\n\nUpdated evidence-led draft.",
        },
        follow_redirects=True,
    )
    assert edited.status_code == 200
    assert b"Updated evidence-led draft" in edited.data
    with app.app_context():
        page = db.session.get(ContentPage, page_id)
        assert page is not None
        assert page.status == "needs_update"
        assert page.generation_mode == "template"
        assert page.seo_title == "Publishing workflow review"
        assert page.body_markdown.endswith("Updated evidence-led draft.")
        assert page.supporting_sources == ["https://docs.example.org/updated"]

    markdown = client.get(f"/content/{page_id}/export.md")
    schema = client.get(f"/content/{page_id}/schema.json")
    assert markdown.status_code == 200
    assert b"Meta description:" in markdown.data
    assert schema.status_code == 200
    assert json.loads(schema.data)["@type"] == "FAQPage"

    duplicate_token = _token(client, f"/content/{page_id}")
    duplicated = client.post(
        f"/content/{page_id}/duplicate",
        data={"csrf_token": duplicate_token},
        follow_redirects=True,
    )
    assert duplicated.status_code == 200
    with app.app_context():
        assert ContentPage.query.count() == 2
        duplicate_page = ContentPage.query.filter(ContentPage.id != page_id).one()
        assert duplicate_page.status == "draft"
        assert duplicate_page.body_markdown.endswith("Updated evidence-led draft.")

    archive_token = _token(client, f"/content/{page_id}")
    archived = client.post(
        f"/content/{page_id}/archive",
        data={"csrf_token": archive_token},
        follow_redirects=True,
    )
    assert archived.status_code == 200
    with app.app_context():
        assert db.session.get(ContentPage, page_id).status == "archived"


def test_missing_llm_key_uses_explicit_template_mode():
    data = {
        "target_question": "How can a team evaluate a workflow?",
        "primary_keyword": "workflow",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Editors",
        "content_type": "how_to",
        "generation_mode": "llm",
        "supporting_sources": "",
        "internal_links": "",
    }
    draft, warning = generate_content_draft(data, api_key=None)
    assert draft["generation_mode"] == "template"
    assert "no external AI model was used" in draft["body_markdown"]
    assert warning and "not configured" in warning


def test_llm_rate_limit_is_reported_without_claiming_success(monkeypatch):
    from app.services import content_drafting

    def rate_limited(*_args, **_kwargs):
        raise HTTPError(
            "https://api.example.test",
            429,
            "rate limited",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(content_drafting, "_open_without_redirects", rate_limited)
    data = {
        "target_question": "How should evidence be reviewed?",
        "primary_keyword": "evidence review",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Editors",
        "content_type": "faq",
        "generation_mode": "llm",
        "supporting_sources": "",
        "internal_links": "",
    }
    with pytest.raises(ContentGenerationError, match="rate limiting"):
        generate_content_draft(data, api_key="test-key")


def test_llm_connection_failure_is_reported(monkeypatch):
    from app.services import content_drafting

    def offline(*_args, **_kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(content_drafting, "_open_without_redirects", offline)
    data = {
        "target_question": "How can teams review a workflow?",
        "primary_keyword": "workflow review",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Editors",
        "content_type": "how_to",
        "generation_mode": "llm",
        "supporting_sources": "",
        "internal_links": "",
    }
    with pytest.raises(ContentGenerationError, match="request failed"):
        generate_content_draft(data, api_key="test-key")


def test_llm_success_uses_configured_api_and_returns_labeled_llm_draft(monkeypatch):
    from app.services import content_drafting

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "seo_title": "Evidence-led workflow guide",
                                        "meta_description": "Review an evidence-led workflow.",
                                        "body_markdown": "# Workflow\n\nA concise draft.",
                                    }
                                )
                            }
                        }
                    ]
                }
            ).encode()

    seen = {}

    def fake_open(request, timeout):
        seen["url"] = request.full_url
        seen["authorization"] = request.get_header("Authorization")
        seen["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(content_drafting, "_open_without_redirects", fake_open)
    data = {
        "target_question": "How can teams review a workflow?",
        "primary_keyword": "workflow review",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Editors",
        "content_type": "how_to",
        "generation_mode": "llm",
        "supporting_sources": "https://docs.example.org/workflow",
        "internal_links": "",
    }
    draft, warning = generate_content_draft(
        data,
        api_key="test-key",
        api_url="https://provider.example.org/v1/chat/completions",
        model="test-model",
        timeout=9,
    )
    assert warning is None
    assert draft["generation_mode"] == "llm"
    assert "External LLM-assisted draft" in draft["body_markdown"]
    assert "https://docs.example.org/workflow" in draft["body_markdown"]
    assert seen == {
        "url": "https://provider.example.org/v1/chat/completions",
        "authorization": "Bearer test-key",
        "timeout": 9,
    }


def test_malformed_llm_response_is_rejected(monkeypatch):
    from app.services import content_drafting

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"not-json"}}]}'

    monkeypatch.setattr(
        content_drafting,
        "_open_without_redirects",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    data = {
        "target_question": "How should claims be checked?",
        "primary_keyword": "claim review",
        "search_intent": "informational",
        "target_market": "US",
        "intended_audience": "Editors",
        "content_type": "how_to",
        "generation_mode": "llm",
        "supporting_sources": "",
        "internal_links": "",
    }
    with pytest.raises(ContentGenerationError, match="did not match"):
        generate_content_draft(data, api_key="test-key")


def test_generate_ten_uses_only_verified_questions_with_saved_sources(client, app):
    with app.app_context():
        user = _create_user()
        for index in range(12):
            db.session.add(
                Question(
                    user_id=user.id,
                    question_text=f"How can creators plan workflow {index}?",
                    topic="Publishing",
                    market="US",
                    source_url=f"https://sources.example.org/{index}",
                    verification_status="verified",
                )
            )
        db.session.add(
            Question(
                user_id=user.id,
                question_text="Unverified question without a citation?",
                market="US",
            )
        )
        db.session.commit()
    _sign_in(client)
    token = _token(client, "/content/")
    response = client.post(
        "/content/generate-ten",
        data={"csrf_token": token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Generated 10" in response.data
    with app.app_context():
        pages = ContentPage.query.order_by(ContentPage.id).all()
        assert len(pages) == 10
        assert all(page.research_question_id and page.supporting_sources for page in pages)
        assert all(page.generation_mode == "template" for page in pages)


def test_audit_recommendation_prefills_content_brief(client, app):
    with app.app_context():
        user = _create_user()
        run = AuditRun(user_id=user.id, audit_type="technical", status="completed")
        db.session.add(run)
        db.session.flush()
        issue = AuditIssue(
            user_id=user.id,
            audit_run_id=run.id,
            title="Add a descriptive page title",
            description="The page has no title element.",
            severity="medium",
            recommendation="Add a unique title element.",
            evidence="Observed title is empty.",
        )
        db.session.add(issue)
        db.session.commit()
        issue_id = issue.id
    _sign_in(client)
    response = client.get(f"/content/new?issue_id={issue_id}")
    assert response.status_code == 200
    assert b"Add a descriptive page title" in response.data
    token = _token(client, f"/content/new?issue_id={issue_id}")
    saved = client.post(
        f"/content/new?issue_id={issue_id}",
        data={
            **_draft_form(
                target_question="How should a page title be improved?",
                primary_keyword="technical SEO",
                intended_audience="Website owners and content editors",
                content_type="brief",
                audit_issue_id=str(issue_id),
            ),
            "csrf_token": token,
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b"Observed evidence: Observed title is empty." in saved.data
    assert b"Add a unique title element." in saved.data


def test_citation_opportunity_evidence_requirements_details_and_export(client, app):
    with app.app_context():
        _create_user()
    _sign_in(client)
    token = _token(client, "/citations/new")
    invalid = client.post(
        "/citations/new",
        data={
            **_citation_form(
                research_status="researched",
                competitor_reference="Competitor X",
            ),
            "csrf_token": token,
        },
    )
    assert invalid.status_code == 400
    assert b"Record relevance evidence" in invalid.data
    assert b"requires a URL" in invalid.data
    with app.app_context():
        assert CitationTarget.query.count() == 0

    valid_token = _token(client, "/citations/new")
    saved = client.post(
        "/citations/new",
        data={
            **_citation_form(
                source_name="Creator directory",
                relevance_evidence="The page lists tools for the target creator audience.",
                evidence_url="https://directory.example.org/about",
                competitor_reference="Competitor X",
                competitor_evidence_url="https://directory.example.org/competitor",
                research_status="researched",
                priority="high",
                notes='=HYPERLINK("https://malicious.example")',
            ),
            "csrf_token": valid_token,
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert b"not a confirmed mention" in saved.data
    assert b"Competitor X" in saved.data
    with app.app_context():
        opportunity = CitationTarget.query.one()
        opportunity_id = opportunity.id
        assert opportunity.research_status == "researched"
        assert opportunity.status == "monitoring"

    exported = client.get("/citations/export.csv")
    assert exported.status_code == 200
    assert b"source_name,url,source_type" in exported.data
    assert b"Creator directory" in exported.data
    assert b"competitor_evidence_url" in exported.data
    assert b"'=HYPERLINK" in exported.data
    assert client.get(f"/citations/{opportunity_id}").status_code == 200


def test_content_and_citation_records_are_tenant_scoped(client, app):
    with app.app_context():
        owner = _create_user()
        other = _create_user("other@example.com")
        page = ContentPage(
            user_id=other.id,
            title="Private draft",
            url="https://draft.visibilityforge.invalid/content/private",
            content_type="brief",
        )
        citation = CitationTarget(
            user_id=other.id,
            domain="private.example.org",
            url="https://private.example.org/page",
        )
        db.session.add_all([page, citation])
        db.session.commit()
        page_id, citation_id = page.id, citation.id
    _sign_in(client)
    assert client.get(f"/content/{page_id}").status_code == 404
    assert client.get(f"/citations/{citation_id}").status_code == 404
