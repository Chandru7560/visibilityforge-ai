import re
import pytest

from app.extensions import db
from app.models import AuditIssue, AuditPage, AuditRun, User
from app.services import technical_audit
from app.services.technical_audit import (
    FetchResult,
    UnsafeDestination,
    run_technical_audit,
    safe_fetch,
    validate_public_url,
    validate_snippet,
)


def _public_resolver(_host, _port):
    return ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://localhost/",
        "http://service.local/",
        "http://metadata.google.internal/",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[64:ff9b::a00:1]/",
        "http://example.com:8080/",
        "https://user:pass@example.com/",
    ],
)
def test_url_validation_rejects_non_public_or_unsafe_destinations(url):
    with pytest.raises(UnsafeDestination):
        validate_public_url(url, resolver=_public_resolver)


def test_url_validation_rejects_hostnames_resolving_to_mixed_addresses():
    def mixed_resolver(_host, _port):
        return ["93.184.216.34", "192.168.1.10"]

    with pytest.raises(UnsafeDestination, match="private"):
        validate_public_url("https://example.com", resolver=mixed_resolver)


def test_url_validation_normalizes_public_url_and_removes_fragment():
    normalized = validate_public_url(
        "HTTPS://Example.COM/path?q=1#fragment",
        resolver=_public_resolver,
    )
    assert normalized == "https://example.com/path?q=1"


class _FakeResponse:
    def __init__(self, status, headers=None, body=b""):
        self.status = status
        self.headers = headers or {}
        self.body = body

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def read(self, amount=-1):
        if amount == 0:
            return b""
        result, self.body = self.body[:amount], self.body[amount:]
        return result


class _FakeConnection:
    responses = []
    requested = []

    def __init__(self, host, port, address, timeout):
        self.host = host
        self.port = port

    def request(self, method, path, headers):
        self.requested.append((method, path, headers))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self):
        pass


def test_safe_fetch_revalidates_redirect_destination_and_caps_body(monkeypatch):
    _FakeConnection.responses = [
        _FakeResponse(302, {"Location": "http://127.0.0.1/private"}),
    ]
    _FakeConnection.requested = []
    monkeypatch.setattr(technical_audit, "_PinnedHTTPConnection", _FakeConnection)
    with pytest.raises(UnsafeDestination):
        safe_fetch("http://example.com/", resolver=_public_resolver)
    assert len(_FakeConnection.requested) == 1

    _FakeConnection.responses = [
        _FakeResponse(
            200,
            {"Content-Type": "text/html", "Content-Length": "20"},
            b"too large",
        )
    ]
    result = safe_fetch(
        "http://example.com/",
        max_bytes=8,
        resolver=_public_resolver,
    )
    assert result.truncated is True
    assert result.body == b""


def test_safe_fetch_rejects_dns_rebinding_before_connect(monkeypatch):
    resolutions = iter(
        [
            ["93.184.216.34"],
            ["93.184.216.34"],
            ["127.0.0.1"],
        ]
    )
    _FakeConnection.requested = []
    monkeypatch.setattr(technical_audit, "_PinnedHTTPConnection", _FakeConnection)
    with pytest.raises(UnsafeDestination, match="private"):
        safe_fetch("http://example.com/", resolver=lambda _host, _port: next(resolutions))
    assert _FakeConnection.requested == []


def _response(url, status=200, body=b"", content_type="text/html; charset=utf-8"):
    return FetchResult(
        requested_url=url,
        final_url=url,
        status_code=status,
        content_type=content_type,
        body=body,
        elapsed_ms=8,
    )


def test_crawler_respects_robots_limits_parses_pages_and_finds_actual_issues(monkeypatch):
    root = "https://example.com/"
    about = "https://example.com/about"
    missing = "https://example.com/missing"
    pages = {
        root: (
            '<html><head><title>Same title</title><meta name="viewport" content="width=device-width">'
            '<meta name="description" content="Homepage"></head><body><h1>Home</h1>'
            '<a href="/about">About</a><a href="/missing">Missing</a><img src="/x.png">'
            '<script type="application/ld+json">{"@context":"https://schema.org"}</script></body></html>'
        ).encode(),
        about: (
            "<html><head><title>Same title</title></head><body>"
            '<a href="/">Home</a><h1>About</h1></body></html>'
        ).encode(),
        missing: b"<html>not found</html>",
    }

    def fake_fetch(url, **_kwargs):
        if url.endswith("/robots.txt"):
            return _response(url, body=b"User-agent: *\nAllow: /\n", content_type="text/plain")
        if url.endswith("/sitemap.xml"):
            return _response(
                url,
                body=b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://example.com/</loc></url></urlset>',
                content_type="application/xml",
            )
        if url.endswith("/llms.txt"):
            return _response(url, status=404, content_type="text/plain")
        if url == root:
            return _response(url, body=pages[root])
        if url == about:
            return _response(url, body=pages[about])
        if url == missing:
            return _response(url, status=404, body=pages[missing])
        raise AssertionError(f"Unexpected URL fetch: {url}")

    monkeypatch.setattr(technical_audit.time, "sleep", lambda _seconds: None)
    result = run_technical_audit(
        root,
        max_pages=3,
        max_depth=1,
        min_request_interval=0.5,
        fetcher=fake_fetch,
        resolver=_public_resolver,
    )
    assert len(result["pages"]) == 3
    assert result["metrics"]["robots_status"] == "200"
    assert result["metrics"]["sitemap_status"] == "found"
    assert result["metrics"]["llms_status"] == "404"
    root_page = next(page for page in result["pages"] if page["url"] == root)
    assert root_page["jsonld_present"] is True
    assert root_page["jsonld_valid"] is True
    assert root_page["images_missing_alt"] == 1
    assert root_page["has_viewport"] is True
    titles = [issue["title"] for issue in result["issues"]]
    assert "Broken internal link observed" in titles
    assert "Duplicate page titles found" in titles
    assert "Meta description is missing" in titles
    assert len(result["recommendations"]) == 20
    assert any(not item["observed"] for item in result["recommendations"])


def test_crawler_does_not_fetch_robots_disallowed_page(monkeypatch):
    requested = []
    root = "https://example.com/"
    blocked = "https://example.com/private"

    def fake_fetch(url, **_kwargs):
        requested.append(url)
        if url.endswith("/robots.txt"):
            return _response(url, body=b"User-agent: *\nDisallow: /private\n", content_type="text/plain")
        if url.endswith("/sitemap.xml"):
            return _response(url, status=404)
        if url.endswith("/llms.txt"):
            return _response(url, status=404)
        if url == root:
            return _response(url, body=b'<a href="/private">private</a><h1>Home</h1>')
        raise AssertionError(f"Disallowed URL fetched: {url}")

    monkeypatch.setattr(technical_audit.time, "sleep", lambda _seconds: None)
    result = run_technical_audit(
        root,
        max_pages=4,
        max_depth=1,
        fetcher=fake_fetch,
        resolver=_public_resolver,
    )
    assert blocked not in requested
    blocked_page = next(page for page in result["pages"] if page["url"] == blocked)
    assert blocked_page["crawl_status"] == "blocked"


@pytest.mark.parametrize(
    ("kind", "snippet", "expected"),
    [
        ("jsonld", '{"@type":"WebPage"}', True),
        ("jsonld", "{invalid json}", False),
        (
            "html_metadata",
            '<title>Page</title><meta name="description" content="Summary"><link rel="canonical" href="https://example.com/">',
            True,
        ),
        ("html_metadata", "<title>Page</title>", False),
    ],
)
def test_local_snippet_validation(kind, snippet, expected):
    assert validate_snippet(kind, snippet)["valid"] is expected


def _auth(client):
    response = client.get("/auth/login")
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', response.data)
    assert match
    return client.post(
        "/auth/login",
        data={
            "csrf_token": match.group(1).decode(),
            "email": "audit@example.com",
            "password": "a-secure-test-password",
        },
        follow_redirects=True,
    )


def test_audit_history_details_and_csv_are_tenant_scoped(app, client, monkeypatch):
    with app.app_context():
        user = User(
            email="audit@example.com",
            full_name="Audit Analyst",
            contact_email="owner@example.com",
        )
        user.set_password("a-secure-test-password")
        db.session.add(user)
        db.session.commit()
    _auth(client)
    monkeypatch.setattr(
        "app.routes.audits.validate_public_url",
        lambda value: "https://example.com/",
    )
    monkeypatch.setattr(
        "app.routes.audits.run_technical_audit",
        lambda *_args, **_kwargs: {
            "root_url": "https://example.com/",
            "pages": [
                {
                    "url": "https://example.com/",
                    "final_url": "https://example.com/",
                    "depth": 0,
                    "crawl_status": "fetched",
                    "status_code": 200,
                    "h1_count": 1,
                    "internal_links": [],
                    "broken_internal_links": [],
                    "response_ms": 8,
                    "response_bytes": 100,
                }
            ],
            "issues": [
                {
                    "category": "metadata",
                    "title": "Page title is missing",
                    "severity": "high",
                    "page_url": "https://example.com/",
                    "evidence": '{"title": null}',
                    "description": "No title was found.",
                    "recommendation": "Add a title.",
                    "code_example": "<title>Page</title>",
                    "validation_method": "Inspect the head.",
                    "validation_status": "not_validated",
                }
            ],
            "recommendations": [
                {
                    "priority": index,
                    "title": f"Recommendation {index}",
                    "observed": index == 1,
                    "severity": "high" if index == 1 else "proposed",
                    "page_url": None,
                    "recommendation": "Review this item.",
                    "evidence": "Observed" if index == 1 else "Proposed",
                }
                for index in range(1, 21)
            ],
            "metrics": {
                "robots_status": "200",
                "sitemap_url": None,
                "sitemap_status": "not_found",
                "llms_status": "404",
                "https_available": True,
            },
            "error_summary": None,
        },
    )
    token_response = client.get("/audits/")
    match = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"', token_response.data)
    assert match
    response = client.post(
        "/audits/",
        data={
            "csrf_token": match.group(1).decode(),
            "website_url": "https://example.com/",
            "max_pages": "20",
            "max_depth": "2",
            "terms_confirmed": "y",
        },
    )
    assert response.status_code == 302
    report = client.get(response.headers["Location"])
    assert report.status_code == 200
    assert b"Page title is missing" in report.data
    assert b"Proposed" in report.data

    with app.app_context():
        run = db.session.scalar(db.select(AuditRun))
        issue = db.session.scalar(db.select(AuditIssue))
        page = db.session.scalar(db.select(AuditPage))
        assert run.status == "completed"
        assert run.pages_crawled == 1
        assert len(run.recommendations) == 20
        assert issue.validation_status == "not_validated"
        assert page.status_code == 200

    issue_page = client.get(f"/audits/issues/{issue.id}")
    assert issue_page.status_code == 200
    assert b"not externally validated" in issue_page.data
    detail_token = re.search(
        rb'name="csrf_token"[^>]*value="([^"]+)"', report.data
    ).group(1).decode()
    validation = client.post(
        f"/audits/{run.id}/validate-snippet",
        data={
            "csrf_token": detail_token,
            "kind": "jsonld",
            "snippet": '{"@context":"https://schema.org","@type":"WebPage"}',
        },
        follow_redirects=True,
    )
    assert validation.status_code == 200
    assert b"Local validation history" in validation.data
    with app.app_context():
        persisted_run = db.session.get(AuditRun, run.id)
        persisted_issue = db.session.get(AuditIssue, issue.id)
        assert persisted_run.validation_checks[-1]["valid"] is True
        assert persisted_issue.validation_status == "not_validated"
    export = client.get(f"/audits/{run.id}/export.csv")
    assert export.status_code == 200
    assert b"timestamp_utc" in export.data
    assert b"Page title is missing" in export.data


def test_audit_form_rejects_private_destination_before_creating_run(app, client):
    with app.app_context():
        user = User(
            email="audit@example.com",
            full_name="Audit Analyst",
            contact_email="owner@example.com",
        )
        user.set_password("a-secure-test-password")
        db.session.add(user)
        db.session.commit()
    _auth(client)
    page = client.get("/audits/")
    csrf_token = re.search(
        rb'name="csrf_token"[^>]*value="([^"]+)"', page.data
    ).group(1).decode()
    response = client.post(
        "/audits/",
        data={
            "csrf_token": csrf_token,
            "website_url": "http://127.0.0.1/",
            "max_pages": "20",
            "max_depth": "2",
            "terms_confirmed": "y",
        },
    )
    assert response.status_code == 400
    with app.app_context():
        assert db.session.scalar(db.select(AuditRun)) is None
