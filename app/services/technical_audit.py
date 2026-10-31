import http.client
import ipaddress
import json
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

USER_AGENT = "VisibilityForgeAuditBot/1.0 (+https://visibilityforge.ai/crawler)"
REDIRECT_CODES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5
SPECIAL_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".test",
    ".invalid",
    ".example",
    ".onion",
    ".lan",
    ".home",
    ".corp",
    ".intranet",
    ".private",
    ".arpa",
)
IPV6_TRANSITION_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
)


class UnsafeDestination(ValueError):
    pass


class CrawlError(RuntimeError):
    pass


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return False
        if any(address in prefix for prefix in IPV6_TRANSITION_PREFIXES):
            return False
    return True


def _resolve_addresses(host: str, port: int) -> list[str]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise UnsafeDestination("The website hostname could not be resolved.") from error
    addresses = list(dict.fromkeys(item[4][0].split("%", 1)[0] for item in results))
    if not addresses:
        raise UnsafeDestination("The website hostname has no network addresses.")
    for value in addresses:
        if not _is_public_address(value):
            raise UnsafeDestination(
                "The website resolves to a private, reserved, or non-public address."
            )
    return addresses


def validate_public_url(
    value: str,
    resolver: Callable[[str, int], list[str]] = _resolve_addresses,
) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise UnsafeDestination("Enter a website URL up to 2,048 characters.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UnsafeDestination("The website URL contains invalid characters.")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as error:
        raise UnsafeDestination("The website URL is malformed.") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UnsafeDestination("Only public HTTP and HTTPS website URLs are supported.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeDestination("Website URLs must not contain credentials.")
    if parsed.fragment:
        parsed = parsed._replace(fragment="")
    scheme = parsed.scheme.lower()
    if port is None:
        port = 443 if scheme == "https" else 80
    if port != (443 if scheme == "https" else 80):
        raise UnsafeDestination("Only the standard HTTP and HTTPS ports are allowed.")

    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise UnsafeDestination("The website hostname is invalid.") from error
    if not host or host == "metadata.google.internal" or host.endswith(
        SPECIAL_HOST_SUFFIXES
    ) or "%" in host:
        raise UnsafeDestination("Internal and reserved hostnames are not allowed.")
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is None:
        if "." not in host or host.isdigit():
            raise UnsafeDestination("Internal or single-label hostnames are not allowed.")
        if host in {"localhost", "metadata.google.internal"}:
            raise UnsafeDestination("Internal hostnames are not allowed.")
    elif not _is_public_address(host):
        raise UnsafeDestination("Private, reserved, and non-public IP addresses are blocked.")

    addresses = resolver(host, port)
    if not addresses:
        raise UnsafeDestination("The website hostname has no public network address.")
    for address_value in addresses:
        if not _is_public_address(address_value):
            raise UnsafeDestination(
                "The website resolves to a private, reserved, or non-public address."
            )

    netloc = f"[{host}]" if ":" in host else host
    if port != (443 if scheme == "https" else 80):
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self.address, self.port), self.timeout, self.source_address
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float):
        super().__init__(
            host,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self.address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self.address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    elapsed_ms: int
    redirect_chain: list[str] = field(default_factory=list)
    truncated: bool = False
    response_bytes: int | None = None


def safe_fetch(
    url: str,
    *,
    timeout: float = 4.0,
    max_bytes: int = 512_000,
    resolver: Callable[[str, int], list[str]] = _resolve_addresses,
    redirect_allowed: Callable[[str], bool] | None = None,
    before_request: Callable[[], None] | None = None,
) -> FetchResult:
    original = validate_public_url(url, resolver)
    current = original
    redirects: list[str] = []
    started = time.monotonic()
    for redirect_number in range(MAX_REDIRECTS + 1):
        current = validate_public_url(current, resolver)
        parsed = urlsplit(current)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = resolver(parsed.hostname or "", port)
        if not addresses:
            raise UnsafeDestination("The website hostname has no network addresses.")
        for resolved_address in addresses:
            if not _is_public_address(resolved_address):
                raise UnsafeDestination(
                    "The website resolves to a private, reserved, or non-public address."
                )
        address = addresses[0].split("%", 1)[0]
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = _PinnedHTTPSConnection(
                parsed.hostname or "", port, address, timeout
            )
        else:
            connection = _PinnedHTTPConnection(
                parsed.hostname or "", port, address, timeout
            )
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            if before_request is not None:
                before_request()
            connection.request(
                "GET",
                path,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml,text/plain,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in REDIRECT_CODES:
                location = response.getheader("Location")
                response.read(1024)
                if not location:
                    raise CrawlError("The website returned a redirect without a destination.")
                if redirect_number >= MAX_REDIRECTS:
                    raise CrawlError("The website exceeded the redirect limit.")
                destination = urljoin(current, location)
                if urlsplit(current).scheme == "https" and urlsplit(destination).scheme != "https":
                    raise UnsafeDestination("Secure HTTPS redirects may not downgrade to HTTP.")
                destination = validate_public_url(destination, resolver)
                if redirect_allowed is not None and not redirect_allowed(destination):
                    raise UnsafeDestination(
                        "A redirect destination is disallowed by the website's robots.txt policy."
                    )
                redirects.append(destination)
                current = destination
                continue
            content_length = response.getheader("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                body = response.read(0)
                return FetchResult(
                    requested_url=original,
                    final_url=current,
                    status_code=response.status,
                    content_type=response.getheader("Content-Type", ""),
                    body=body,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    redirect_chain=redirects,
                    truncated=True,
                    response_bytes=int(content_length),
                )
            body = response.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            return FetchResult(
                requested_url=original,
                final_url=current,
                status_code=response.status,
                content_type=response.getheader("Content-Type", ""),
                body=body[:max_bytes],
                elapsed_ms=int((time.monotonic() - started) * 1000),
                redirect_chain=redirects,
                truncated=truncated,
                response_bytes=(
                    int(content_length)
                    if content_length and content_length.isdigit()
                    else len(body)
                ),
            )
        except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError) as error:
            raise CrawlError(
                f"The public page could not be fetched: {type(error).__name__}."
            ) from error
        finally:
            connection.close()
    raise CrawlError("The website exceeded the redirect limit.")


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.title_depth = 0
        self.description: str | None = None
        self.canonical: str | None = None
        self.h1_count = 0
        self.viewport = False
        self.links: list[str] = []
        self.images_total = 0
        self.images_missing_alt = 0
        self.jsonld_parts: list[str] = []
        self._jsonld_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self.title_depth += 1
        elif tag == "meta":
            name = (values.get("name") or "").lower()
            if name == "description" and self.description is None:
                self.description = values.get("content")
            if name == "viewport":
                self.viewport = bool(values.get("content"))
        elif tag == "link" and "canonical" in (
            (values.get("rel") or "").lower().split()
        ):
            self.canonical = values.get("href")
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "a" and values.get("href") and len(self.links) < 1000:
            self.links.append(values["href"] or "")
        elif tag == "img":
            self.images_total += 1
            if values.get("alt") is None:
                self.images_missing_alt += 1
        elif tag == "script" and "application/ld+json" in (
            (values.get("type") or "").lower()
        ):
            self._jsonld_depth += 1
            self.jsonld_parts.append("")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        elif tag == "script" and self._jsonld_depth:
            self._jsonld_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_parts.append(data)
        if self._jsonld_depth and self.jsonld_parts:
            self.jsonld_parts[-1] += data

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())

    @property
    def jsonld_valid(self) -> bool:
        if not self.jsonld_parts:
            return False
        try:
            for content in self.jsonld_parts:
                json.loads(content)
        except (json.JSONDecodeError, TypeError, RecursionError):
            return False
        return True


def _normalize_link(value: str, page_url: str, root: str) -> str | None:
    if not value or value.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    absolute = urljoin(page_url, value.strip())
    try:
        target = urlsplit(absolute)
        origin = urlsplit(root)
        target_port = target.port
        if target_port is None:
            target_port = 443 if target.scheme == "https" else 80
        origin_port = origin.port
        if origin_port is None:
            origin_port = 443 if origin.scheme == "https" else 80
        target_host = (target.hostname or "").encode("idna").decode("ascii").lower()
        origin_host = (origin.hostname or "").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None
    if (
        target.scheme.lower() not in {"http", "https"}
        or target.scheme.lower() != origin.scheme.lower()
        or target_host != origin_host
        or target_port != origin_port
        or target.username is not None
        or target.password is not None
    ):
        return None
    return urlunsplit(
        (origin.scheme, origin.netloc, target.path or "/", target.query, "")
    )


def _issue(
    *,
    category: str,
    title: str,
    severity: str,
    page_url: str | None,
    evidence: Any,
    explanation: str,
    recommendation: str,
    code_example: str | None = None,
    validation_method: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "title": title,
        "severity": severity,
        "page_url": page_url,
        "evidence": json.dumps(evidence, ensure_ascii=True)[:5000],
        "description": explanation,
        "recommendation": recommendation,
        "code_example": code_example,
        "validation_method": validation_method,
        "validation_status": "not_validated",
    }


def validate_snippet(kind: str, snippet: str) -> dict[str, Any]:
    if not isinstance(snippet, str) or not snippet.strip() or len(snippet) > 50_000:
        return {"valid": False, "details": ["Enter a non-empty snippet up to 50 KB."]}
    if kind == "jsonld":
        try:
            value = json.loads(snippet)
        except json.JSONDecodeError as error:
            return {
                "valid": False,
                "details": [f"Invalid JSON at line {error.lineno}, column {error.colno}."],
            }
        except RecursionError:
            return {"valid": False, "details": ["JSON nesting exceeded the local parser limit."]}
        return {
            "valid": isinstance(value, (dict, list)),
            "details": ["Valid JSON syntax; schema semantics and external rules were not checked."]
            if isinstance(value, (dict, list))
            else ["JSON-LD must be a JSON object or array."],
        }
    if kind == "html_metadata":
        parser = _PageParser()
        try:
            parser.feed(snippet)
            parser.close()
        except ValueError as error:
            return {"valid": False, "details": [f"HTML could not be parsed: {type(error).__name__}."]}
        missing = []
        if not parser.title:
            missing.append("A non-empty <title> is missing.")
        if not parser.description:
            missing.append("A meta description is missing.")
        if not parser.canonical:
            missing.append("A canonical link is missing.")
        return {
            "valid": not missing,
            "details": missing or ["Title, meta description, and canonical link were parsed locally."],
        }
    return {"valid": False, "details": ["Choose JSON-LD or HTML metadata validation."]}


PROPOSED_RECOMMENDATIONS = (
    ("Review XML sitemap coverage", "Confirm the sitemap lists canonical, indexable URLs."),
    ("Review robots directives", "Check that important public pages are not disallowed."),
    ("Add structured data where appropriate", "Use schema types that accurately describe page content."),
    ("Review mobile usability", "Test templates at narrow viewport widths."),
    ("Review image delivery", "Use descriptive alt text and appropriately sized image files."),
    ("Review internal navigation", "Connect related pages with descriptive, crawlable links."),
    ("Review redirect policy", "Keep permanent redirects direct and remove obsolete hops."),
    ("Review canonical consistency", "Ensure canonical URLs agree with internal links and redirects."),
    ("Review metadata uniqueness", "Write distinct titles and descriptions for important pages."),
    ("Review server performance", "Measure real-user performance with an authorized field-data source."),
    ("Review page accessibility", "Test content with keyboard and assistive technologies."),
    ("Review content freshness", "Confirm important pages remain accurate and useful."),
    ("Review crawlable links", "Use normal anchor elements for navigational links."),
    ("Review HTTPS configuration", "Verify certificate coverage and secure deployment settings."),
    ("Review index eligibility", "Use an authorized search-console source; fetchability is not indexing."),
    ("Review pagination strategy", "Ensure paginated content has useful navigation and canonical handling."),
    ("Review JavaScript rendering", "Check important content in a supported rendered browser."),
    ("Review language annotations", "Add accurate language and hreflang data when applicable."),
    ("Review sitemap freshness", "Regenerate sitemaps when canonical content changes."),
    ("Review search snippets", "Check descriptions for accuracy and avoid unsupported promises."),
)


def _build_recommendations(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    ordered = sorted(
        enumerate(issues),
        key=lambda item: (priority.get(item[1]["severity"], 4), item[0]),
    )
    recommendations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, issue in ordered:
        key = issue["title"].casefold()
        if key in seen:
            continue
        seen.add(key)
        recommendations.append(
            {
                "priority": len(recommendations) + 1,
                "title": issue["title"],
                "observed": True,
                "severity": issue["severity"],
                "page_url": issue["page_url"],
                "recommendation": issue["recommendation"],
                "evidence": issue["evidence"],
            }
        )
        if len(recommendations) == 20:
            return recommendations
    for title, recommendation in PROPOSED_RECOMMENDATIONS:
        if title.casefold() in seen:
            continue
        recommendations.append(
            {
                "priority": len(recommendations) + 1,
                "title": title,
                "observed": False,
                "severity": "proposed",
                "page_url": None,
                "recommendation": recommendation,
                "evidence": "Proposed review item; not observed as a defect in this crawl.",
            }
        )
        if len(recommendations) == 20:
            break
    return recommendations


def run_technical_audit(
    root_url: str,
    *,
    max_pages: int = 20,
    max_depth: int = 2,
    timeout: float = 4.0,
    max_response_bytes: int = 512_000,
    min_request_interval: float = 0.75,
    max_duration_seconds: float = 20.0,
    fetcher: Callable[..., FetchResult] = safe_fetch,
    resolver: Callable[[str, int], list[str]] = _resolve_addresses,
) -> dict[str, Any]:
    root = validate_public_url(root_url, resolver)
    parsed_root = urlsplit(root)
    origin = f"{parsed_root.scheme}://{parsed_root.netloc}/"
    max_pages = min(max(int(max_pages), 1), 50)
    max_depth = min(max(int(max_depth), 0), 3)
    min_request_interval = max(float(min_request_interval), 0.5)
    max_duration_seconds = min(max(float(max_duration_seconds), 5.0), 25.0)
    run_started = time.monotonic()
    last_request = 0.0
    issues: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {
        "robots_status": "unknown",
        "sitemap_url": None,
        "sitemap_status": "unknown",
        "llms_status": "unknown",
        "https_available": None,
    }

    def fetch_limited(url: str) -> FetchResult:
        nonlocal last_request

        def before_request() -> None:
            nonlocal last_request
            wait = min_request_interval - (time.monotonic() - last_request)
            if last_request and wait > 0:
                time.sleep(wait)
            if time.monotonic() - run_started >= max_duration_seconds:
                raise CrawlError("The configured total audit time limit was reached.")
            last_request = time.monotonic()

        result = fetcher(
            url,
            timeout=timeout,
            max_bytes=max_response_bytes,
            resolver=resolver,
            redirect_allowed=lambda destination: (
                urlsplit(destination).hostname == urlsplit(root).hostname
                and (
                    robots_text is None
                    or parser.can_fetch(USER_AGENT, destination)
                )
            ),
            before_request=before_request,
        )
        return result

    robots_url = urljoin(origin, "robots.txt")
    parser = RobotFileParser()
    robots_text: str | None = None
    try:
        robots = fetch_limited(robots_url)
        metrics["robots_status"] = str(robots.status_code)
        if 200 <= robots.status_code < 300:
            robots_text = robots.body.decode("utf-8", errors="replace")
            parser.set_url(robots_url)
            parser.parse(robots_text.splitlines())
        elif robots.status_code >= 500 or robots.status_code in {401, 403, 429}:
            raise CrawlError(
                f"robots.txt returned HTTP {robots.status_code}; crawling stopped conservatively."
            )
        else:
            parser.set_url(robots_url)
            parser.parse([])
    except (CrawlError, UnsafeDestination) as error:
        issues.append(
            _issue(
                category="robots",
                title="Robots policy could not be confirmed",
                severity="high",
                page_url=robots_url,
                evidence={"status": metrics["robots_status"], "error": str(error)},
                explanation="The crawler stopped because site crawling permissions could not be safely determined.",
                recommendation="Restore a readable robots.txt policy, then rerun the audit. Review the website's terms before crawling.",
                validation_method="Request robots.txt from the site and review the crawler rules.",
            )
        )
        return {
            "root_url": root,
            "pages": pages,
            "issues": issues,
            "recommendations": _build_recommendations(issues),
            "metrics": metrics,
            "error_summary": str(error),
        }

    def allowed(url: str) -> bool:
        try:
            normalized = validate_public_url(url, resolver)
        except UnsafeDestination:
            return False
        return parser.can_fetch(USER_AGENT, normalized)

    sitemap_candidate: str | None = None
    if robots_text:
        for line in robots_text.splitlines():
            if line.lower().startswith("sitemap:"):
                candidate = line.split(":", 1)[1].strip()
                try:
                    sitemap_candidate = validate_public_url(
                        urljoin(origin, candidate), resolver
                    )
                except UnsafeDestination:
                    sitemap_candidate = None
                if sitemap_candidate:
                    break
    if sitemap_candidate is None:
        sitemap_candidate = urljoin(origin, "sitemap.xml")
    if urlsplit(sitemap_candidate).hostname != parsed_root.hostname:
        sitemap_candidate = urljoin(origin, "sitemap.xml")
    if allowed(sitemap_candidate):
        try:
            sitemap = fetch_limited(sitemap_candidate)
            metrics["sitemap_status"] = (
                "not_found"
                if sitemap.status_code == 404
                else str(sitemap.status_code)
            )
            if 200 <= sitemap.status_code < 300:
                metrics["sitemap_status"] = "invalid"
            if (
                200 <= sitemap.status_code < 300
                and not sitemap.truncated
                and b"<!DOCTYPE" not in sitemap.body.upper()
                and b"<!ENTITY" not in sitemap.body.upper()
            ):
                try:
                    root_element = ET.fromstring(sitemap.body)
                except ET.ParseError:
                    root_element = None
                if root_element is not None and root_element.tag.rsplit("}", 1)[-1] in {
                    "urlset",
                    "sitemapindex",
                }:
                    metrics["sitemap_url"] = sitemap.final_url
                    metrics["sitemap_status"] = "found"
                elif 200 <= sitemap.status_code < 300:
                    metrics["sitemap_status"] = "invalid"
        except (CrawlError, UnsafeDestination):
            metrics["sitemap_status"] = "unavailable"
    else:
        metrics["sitemap_status"] = "disallowed"
    if metrics["sitemap_status"] in {
        "not_found",
        "invalid",
        "unavailable",
    } or (
        metrics["sitemap_status"].isdigit()
        and not 200 <= int(metrics["sitemap_status"]) < 300
    ):
        metrics["sitemap_status"] = (
            "not_found"
            if metrics["sitemap_status"] == "not_found"
            else "unavailable"
        )
        issues.append(
            _issue(
                category="sitemap",
                title="XML sitemap was not discovered",
                severity="low",
                page_url=sitemap_candidate,
                evidence={"requested_url": sitemap_candidate},
                explanation="No accessible sitemap was found at the declared location or the conventional /sitemap.xml path.",
                recommendation="Publish an XML sitemap containing canonical, public URLs and reference it from robots.txt.",
                code_example="Sitemap: https://www.example.com/sitemap.xml",
                validation_method="Request the sitemap URL and confirm it returns a valid XML sitemap.",
            )
        )

    llms_url = urljoin(origin, "llms.txt")
    if allowed(llms_url):
        try:
            llms = fetch_limited(llms_url)
            metrics["llms_status"] = str(llms.status_code)
        except (CrawlError, UnsafeDestination):
            metrics["llms_status"] = "unavailable"
    else:
        metrics["llms_status"] = "disallowed"
    if metrics["llms_status"] not in {"200", "204", "disallowed"}:
        issues.append(
            _issue(
                category="llms",
                title="llms.txt was not found",
                severity="low",
                page_url=llms_url,
                evidence={"status": metrics["llms_status"]},
                explanation="No successful response was observed at /llms.txt. This optional convention is not a search-indexing requirement.",
                recommendation="If useful for your publishing policy, provide a plain-text /llms.txt with accurate links to authoritative content.",
                validation_method="Request /llms.txt and review its contents for accuracy; this is optional guidance, not an indexing signal.",
            )
        )

    https_root = (
        f"https://{parsed_root.netloc}{parsed_root.path or '/'}"
        + (f"?{parsed_root.query}" if parsed_root.query else "")
    )
    if parsed_root.scheme != "https":
        if allowed(https_root):
            try:
                https_result = fetch_limited(https_root)
                metrics["https_available"] = 200 <= https_result.status_code < 400
            except (CrawlError, UnsafeDestination):
                metrics["https_available"] = False
        else:
            metrics["https_available"] = None
        if not metrics["https_available"]:
            issues.append(
                _issue(
                    category="https",
                    title="HTTPS was not reachable for the starting URL",
                    severity="high",
                    page_url=https_root,
                    evidence={"https_available": False},
                    explanation="The equivalent HTTPS URL could not be fetched successfully from this environment.",
                    recommendation="Enable HTTPS with a valid certificate and redirect HTTP requests to the HTTPS canonical URL.",
                    validation_method="Load the HTTPS URL in a browser and confirm the certificate and redirect behavior.",
                )
            )

    queue = deque([(root, 0)])
    queued = {root}
    visited: set[str] = set()
    error_summary = None
    while queue and len(pages) < max_pages:
        if time.monotonic() - run_started >= max_duration_seconds:
            error_summary = (
                f"The audit reached its {max_duration_seconds:g}-second total time limit."
            )
            break
        url, depth = queue.popleft()
        if url in visited:
            continue
        visited.add(url)
        if not allowed(url):
            pages.append(
                {
                    "url": url,
                    "final_url": None,
                    "depth": depth,
                    "crawl_status": "blocked",
                    "error_summary": "Disallowed by robots.txt.",
                }
            )
            issues.append(
                _issue(
                    category="robots",
                    title="Page disallowed by robots.txt",
                    severity="low",
                    page_url=url,
                    evidence={"robots_url": robots_url},
                    explanation="This URL is disallowed by the site's robots.txt rules and was not fetched.",
                    recommendation="No correction is needed unless this page is intended to be crawled; review the site's policy before changing it.",
                    validation_method="Review the matching robots.txt rule.",
                )
            )
            continue
        try:
            result = fetch_limited(url)
        except (CrawlError, UnsafeDestination) as error:
            pages.append(
                {
                    "url": url,
                    "final_url": None,
                    "depth": depth,
                    "crawl_status": "failed",
                    "error_summary": str(error),
                }
            )
            issues.append(
                _issue(
                    category="http",
                    title="Page could not be fetched",
                    severity="high",
                    page_url=url,
                    evidence={"error": str(error)},
                    explanation="The page did not return an inspectable response within the safety and timeout limits.",
                    recommendation="Check public availability, DNS, TLS configuration, and server response time from a permitted network.",
                    validation_method="Request the URL manually and inspect the response status and TLS connection.",
                )
            )
            continue
        if result.elapsed_ms > 2_000:
            issues.append(
                _issue(
                    category="performance",
                    title="Page response exceeded the 2-second audit heuristic",
                    severity="low",
                    page_url=url,
                    evidence={"response_ms": result.elapsed_ms},
                    explanation="The observed request duration exceeded this audit's basic 2-second heuristic; a single crawl is not a field-performance measurement.",
                    recommendation="Review server response time and use an authorized real-user performance dataset before prioritizing broad performance changes.",
                    validation_method="Repeat measurements from representative regions and compare against real-user field data.",
                )
            )
        content_type = result.content_type.lower()
        entry: dict[str, Any] = {
            "url": url,
            "final_url": result.final_url,
            "depth": depth,
            "crawl_status": "fetched",
            "status_code": result.status_code,
            "content_type": result.content_type[:160],
            "response_bytes": (
                result.response_bytes
                if result.response_bytes is not None
                else len(result.body)
            ),
            "response_ms": result.elapsed_ms,
            "redirect_chain": result.redirect_chain,
            "internal_links": [],
            "broken_internal_links": [],
        }
        pages.append(entry)
        if not 200 <= result.status_code < 300:
            issues.append(
                _issue(
                    category="http",
                    title=f"Page returned HTTP {result.status_code}",
                    severity="high" if result.status_code >= 400 else "medium",
                    page_url=url,
                    evidence={"status_code": result.status_code, "final_url": result.final_url},
                    explanation="The page did not return a successful HTTP response.",
                    recommendation="Resolve server errors, or update internal links to point directly to the intended working URL.",
                    validation_method="Request the page and verify an expected 2xx response.",
                )
            )
        if result.redirect_chain:
            issues.append(
                _issue(
                    category="redirect",
                    title="Page uses a redirect chain",
                    severity="low" if len(result.redirect_chain) == 1 else "medium",
                    page_url=url,
                    evidence={"chain": [url, *result.redirect_chain]},
                    explanation=f"The request traversed {len(result.redirect_chain)} redirect(s) before reaching the final URL.",
                    recommendation="Update internal links to target the final URL and remove unnecessary redirect hops.",
                    validation_method="Request the original URL and inspect each Location response until the final destination.",
                )
            )
        if result.truncated:
            entry["error_summary"] = f"Response exceeded the {max_response_bytes}-byte limit."
            issues.append(
                _issue(
                    category="performance",
                    title="Response exceeded the configured size limit",
                    severity="medium",
                    page_url=url,
                    evidence={"limit_bytes": max_response_bytes},
                    explanation="The body exceeded the crawler's configured safety limit and was not parsed.",
                    recommendation="Review transfer size and ensure the HTML document can be served efficiently.",
                    validation_method="Measure the uncompressed HTML response body size.",
                )
            )
            continue
        if "html" not in content_type and "xhtml" not in content_type:
            continue
        if result.response_bytes is not None and result.response_bytes > 100_000:
            issues.append(
                _issue(
                    category="performance",
                    title="Page HTML response is larger than 100 KB",
                    severity="low",
                    page_url=url,
                    evidence={"response_bytes": result.response_bytes},
                    explanation="The uncompressed HTML body exceeded the audit's 100 KB review heuristic.",
                    recommendation="Review markup, inline scripts, and embedded data; this finding alone does not establish a performance problem.",
                    validation_method="Measure the uncompressed HTML document body and assess it alongside browser performance data.",
                )
            )
        parser_page = _PageParser()
        try:
            parser_page.feed(result.body.decode("utf-8", errors="replace"))
            parser_page.close()
        except ValueError:
            entry["error_summary"] = "The HTML parser could not process this response."
            continue
        links = sorted(
            {
                link
                for raw_link in parser_page.links[:200]
                if (link := _normalize_link(raw_link, result.final_url, origin))
                is not None
            }
        )
        entry.update(
            {
                "title": parser_page.title or None,
                "title_length": len(parser_page.title),
                "meta_description": parser_page.description,
                "canonical_url": (
                    urljoin(result.final_url, parser_page.canonical)
                    if parser_page.canonical
                    else None
                ),
                "h1_count": parser_page.h1_count,
                "internal_links": links,
                "has_viewport": parser_page.viewport,
                "jsonld_present": bool(parser_page.jsonld_parts),
                "jsonld_valid": parser_page.jsonld_valid,
                "images_total": parser_page.images_total,
                "images_missing_alt": parser_page.images_missing_alt,
            }
        )
        if not parser_page.title:
            issues.append(
                _issue(
                    category="metadata",
                    title="Page title is missing",
                    severity="high",
                    page_url=url,
                    evidence={"title": None},
                    explanation="No non-empty HTML title was found.",
                    recommendation="Add a unique, descriptive title that accurately summarizes the page.",
                    code_example="<title>Accurate page topic | Brand</title>",
                    validation_method="Inspect the rendered document head for exactly one non-empty title.",
                )
            )
        elif len(parser_page.title) > 60:
            issues.append(
                _issue(
                    category="metadata",
                    title="Page title is unusually long",
                    severity="low",
                    page_url=url,
                    evidence={"title": parser_page.title, "length": len(parser_page.title)},
                    explanation="The title exceeds the audit's 60-character heuristic; display truncation varies by search context.",
                    recommendation="Consider a concise, accurate title while preserving the page's main topic.",
                    validation_method="Review the title in the HTML source and search-result previews.",
                )
            )
        if not parser_page.description:
            issues.append(
                _issue(
                    category="metadata",
                    title="Meta description is missing",
                    severity="medium",
                    page_url=url,
                    evidence={"meta_description": None},
                    explanation="No meta description was found in the page head.",
                    recommendation="Add a unique, accurate summary of the page; search engines may choose a different snippet.",
                    code_example='<meta name="description" content="A concise, accurate summary of this page.">',
                    validation_method="Inspect the HTML head for a non-empty meta description.",
                )
            )
        if not parser_page.canonical:
            issues.append(
                _issue(
                    category="canonical",
                    title="Canonical link is missing",
                    severity="medium",
                    page_url=url,
                    evidence={"canonical": None},
                    explanation="No canonical link element was found.",
                    recommendation="When the page has a preferred URL, add a self-referencing canonical that resolves to the intended public URL.",
                    code_example=f'<link rel="canonical" href="{result.final_url}">',
                    validation_method="Follow the canonical URL and compare it with the page's preferred public address.",
                )
            )
        if parser_page.h1_count == 0:
            issues.append(
                _issue(
                    category="headings",
                    title="Page has no H1 heading",
                    severity="medium",
                    page_url=url,
                    evidence={"h1_count": 0},
                    explanation="No H1 element was found in the fetched HTML.",
                    recommendation="Provide a clear primary heading that describes the page content.",
                    code_example="<h1>Primary page heading</h1>",
                    validation_method="Inspect the rendered page structure for a meaningful primary heading.",
                )
            )
        if not parser_page.viewport:
            issues.append(
                _issue(
                    category="mobile",
                    title="Mobile viewport metadata is missing",
                    severity="medium",
                    page_url=url,
                    evidence={"viewport_present": False},
                    explanation="No non-empty viewport meta tag was found.",
                    recommendation="Add the responsive viewport declaration to the document head.",
                    code_example='<meta name="viewport" content="width=device-width, initial-scale=1">',
                    validation_method="Inspect the document head and test the layout at a narrow viewport.",
                )
            )
        if parser_page.jsonld_parts and not parser_page.jsonld_valid:
            issues.append(
                _issue(
                    category="structured_data",
                    title="JSON-LD contains invalid JSON",
                    severity="medium",
                    page_url=url,
                    evidence={"script_count": len(parser_page.jsonld_parts)},
                    explanation="At least one JSON-LD script could not be parsed as JSON.",
                    recommendation="Correct the JSON syntax and ensure markup accurately represents visible page content.",
                    code_example='<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Page title"}</script>',
                    validation_method="Parse each JSON-LD script locally, then use an authorized validator if one is configured.",
                )
            )
        elif not parser_page.jsonld_parts:
            issues.append(
                _issue(
                    category="structured_data",
                    title="No JSON-LD structured data found",
                    severity="low",
                    page_url=url,
                    evidence={"jsonld_present": False},
                    explanation="No JSON-LD scripts were observed; structured data is optional and should only be added when accurate.",
                    recommendation="Consider relevant structured data only when it truthfully describes the visible content.",
                    code_example='<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Page title"}</script>',
                    validation_method="Validate JSON syntax locally and check semantic eligibility with an authorized external validator.",
                )
            )
        if parser_page.images_missing_alt:
            issues.append(
                _issue(
                    category="images",
                    title="Images are missing alternative text",
                    severity="medium",
                    page_url=url,
                    evidence={
                        "images_total": parser_page.images_total,
                        "images_missing_alt": parser_page.images_missing_alt,
                    },
                    explanation="One or more image elements have no alt attribute. Intentionally empty alt text remains valid for decorative images and is not counted as missing.",
                    recommendation="Add concise contextual alt text to informative images; keep decorative images intentionally empty and hidden from assistive technology where appropriate.",
                    code_example='<img src="diagram.webp" alt="Annotated diagram of the publishing workflow">',
                    validation_method="Inspect each image in context and check its accessible name with an accessibility tool.",
                )
            )
        if len(links) < 2:
            issues.append(
                _issue(
                    category="internal_links",
                    title="Few internal-link opportunities were found",
                    severity="low",
                    page_url=url,
                    evidence={"internal_link_count": len(links)},
                    explanation="Fewer than two same-origin anchor links were found in this page's HTML.",
                    recommendation="Where helpful to readers, link to related pages using descriptive anchor text.",
                    code_example='<a href="/related-guide">Read the related guide</a>',
                    validation_method="Review rendered navigation and contextual links; exclude non-navigational controls.",
                )
            )
        if depth < max_depth:
            for link in links:
                if link not in queued and link not in visited and len(queued) < max_pages * 5:
                    queue.append((link, depth + 1))
                    queued.add(link)

    title_groups: dict[str, list[str]] = defaultdict(list)
    for page in pages:
        if page.get("crawl_status") == "fetched" and page.get("title"):
            title_groups[page["title"].casefold()].append(page["url"])
    for title, urls in title_groups.items():
        if len(urls) > 1:
            issues.append(
                _issue(
                    category="metadata",
                    title="Duplicate page titles found",
                    severity="medium",
                    page_url=urls[0],
                    evidence={"title": title, "pages": urls},
                    explanation=f"The same title was observed on {len(urls)} crawled pages.",
                    recommendation="Write distinct, page-specific titles that accurately describe each URL.",
                    validation_method="Compare title elements across the listed pages.",
                )
            )

    page_by_url = {page["url"]: page for page in pages}
    checked_targets: dict[str, tuple[int | None, str | None, str]] = {}
    for page in pages:
        checked_targets[page["url"]] = (
            page.get("status_code"),
            page.get("final_url"),
            page.get("crawl_status", "failed"),
        )
    for page in pages:
        for link in page.get("internal_links", []):
            status, _target_url, crawl_status = checked_targets.get(
                link, (None, None, "unknown")
            )
            if crawl_status in {"blocked", "unknown"}:
                continue
            if status is not None and 200 <= status < 400:
                continue
            broken = {"url": link, "status_code": status}
            page.setdefault("broken_internal_links", []).append(broken)
            issues.append(
                _issue(
                    category="internal_links",
                    title="Broken internal link observed",
                    severity="high" if status is None or status >= 400 else "medium",
                    page_url=page["url"],
                    evidence=broken,
                    explanation="A same-origin link returned an error or could not be fetched within the configured crawl budget.",
                    recommendation="Fix the destination, update the link to a working URL, or remove the link.",
                    validation_method="Request the linked URL and verify it returns the intended page without an error.",
                )
            )

    if parsed_root.scheme == "https":
        root_page = next((page for page in pages if page["url"] == root), None)
        if root_page is not None:
            metrics["https_available"] = root_page["crawl_status"] == "fetched"
    recommendations = _build_recommendations(issues)
    return {
        "root_url": root,
        "pages": pages,
        "issues": issues,
        "recommendations": recommendations,
        "metrics": metrics,
        "error_summary": error_summary,
    }
