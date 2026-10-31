import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ContentGenerationError(RuntimeError):
    pass


CONTENT_TYPES = {
    "how_to": "How-to guide",
    "comparison": "Product comparison",
    "faq": "FAQ page",
    "glossary": "Glossary page",
    "brief": "Content brief",
}


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, _request, _response, _code, _message, _headers, _url):
        return None


def _open_without_redirects(request: Request, timeout: float):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:220].rstrip("-") or "content-draft"


def _clean_urls(value: str | list[str] | None) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _source_markdown(sources: list[str], internal_links: list[str]) -> str:
    sections = []
    if sources:
        sections.extend(
            ["## Sources to review", *[f"- [{url}]({url})" for url in sources]]
        )
    if internal_links:
        sections.extend(
            ["## Suggested internal links", *[f"- [{url}]({url})" for url in internal_links]]
        )
    return "\n\n".join(sections)


def _template_body(data: dict[str, str], sources: list[str], internal_links: list[str]) -> str:
    question = data["target_question"].strip()
    keyword = data["primary_keyword"].strip()
    audience = data["intended_audience"].strip()
    market = data["target_market"].strip()
    opening = (
        f"For “{question}”, start by identifying the outcome and constraints that matter to "
        f"{audience}, then compare options against the same criteria. Verify market-specific "
        "and product claims with current primary sources before deciding; this template does "
        "not make or validate those claims."
    )
    kind = data["content_type"]
    if kind == "how_to":
        sections = [
            f"# {question}",
            opening,
            "## Before you begin",
            f"Define the outcome you need, the audience you serve, and the constraints that apply in {market}. "
            "Record the source and date for any product, pricing, or performance claim.",
            "## A practical process",
            "### 1. Establish the baseline",
            f"Write down the current workflow and the specific task related to {keyword}.",
            "### 2. Compare relevant options",
            "Assess documented capabilities against the same criteria. Separate observed facts from estimates.",
            "### 3. Test and review",
            "Run a small, representative test, capture evidence, and review the result with the intended users.",
            "## Common mistakes",
            "Avoid relying on feature claims without a current source, comparing unlike plans, or treating a single test as a universal result.",
            "## Checklist",
            "- Confirm the goal and audience.\n- Check current primary sources.\n- Record test conditions and limitations.",
        ]
    elif kind == "comparison":
        sections = [
            f"# {question}",
            opening,
            "## Compare on the same criteria",
            "Use a current, primary source for every product capability, price, plan limit, and availability statement.",
            "### Comparison table to complete",
            "| Criterion | Option A | Option B | Evidence / date |\n|---|---|---|---|\n| Core workflow | Verify | Verify | Add source |\n| Availability in market | Verify | Verify | Add source |\n| Cost and plan limits | Verify | Verify | Add source |",
            "## How to choose",
            f"Match the decision to the needs of {audience}; do not assume one option is best for every workflow.",
            "## Limitations and verification",
            "Product features, prices, and availability change. Confirm each entry from the vendor before publication.",
        ]
    elif kind == "faq":
        sections = [
            f"# Frequently asked questions: {keyword}",
            f"## {question}",
            opening,
            "## What should readers verify first?",
            "Check current primary documentation for features, eligibility, pricing, and regional availability.",
            "## How should alternatives be compared?",
            "Use the same criteria and test conditions, and cite the source and review date for each factual claim.",
            "## What information is still needed?",
            "Add market-specific details, independently checked sources, and real examples from the intended audience.",
        ]
    elif kind == "glossary":
        sections = [
            f"# {keyword}: a practical glossary entry",
            opening,
            f"## What does {keyword} mean?",
            f"Define “{keyword}” using an authoritative source appropriate to {market}. Add a citation and distinguish the formal definition from this guide's practical explanation.",
            "## Why does it matter?",
            f"Explain how the concept affects {audience}, using a concrete example that has been checked against the cited source.",
            "## Related terms",
            "Add related terms only when their definitions and relationships are supported by reliable references.",
        ]
    else:
        sections = [
            f"# Content brief: {question}",
            opening,
            "## Audience and intent",
            f"Audience: {audience}. Market: {market}. Search intent: {data['search_intent']}.",
            "## Recommended outline",
            f"### H1: {question}\n### H2: Context and definitions\n### H2: Evidence-backed guidance\n### H2: Limitations and next steps",
            "## Evidence plan",
            "Support each non-obvious factual statement with a source that was checked by an editor. Record publication or access dates.",
        ]
    sections.append(
        "## Editorial verification note\n"
        "Template-based draft; no external AI model was used. This is a working draft, not a published or fact-checked article. "
        "Verify all product, pricing, availability, comparative, and market-specific claims before publication."
    )
    audit_context = (data.get("audit_context") or "").strip()
    if audit_context:
        sections.insert(
            2,
            "## Linked technical audit recommendation\n"
            + audit_context
            + "\n\nUse the cited audit record as project context; re-check the affected page before making implementation claims.",
        )
    source_section = _source_markdown(sources, internal_links)
    if source_section:
        sections.append(source_section)
    return "\n\n".join(sections)


def _schema_for(data: dict[str, str], seo_title: str, meta_description: str) -> dict | None:
    if data["content_type"] == "faq":
        return {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "name": seo_title,
            "description": meta_description,
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": data["target_question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": "Use the linked sources and editorial verification note in this draft to confirm a complete answer before publication.",
                    },
                }
            ],
        }
    if data["content_type"] == "how_to":
        return {
            "@context": "https://schema.org",
            "@type": "HowTo",
            "name": seo_title,
            "description": meta_description,
            "step": [
                {"@type": "HowToStep", "name": "Define the goal", "text": "Document the audience, desired outcome, and constraints."},
                {"@type": "HowToStep", "name": "Check evidence", "text": "Verify relevant claims against current primary sources."},
                {"@type": "HowToStep", "name": "Review the result", "text": "Test the approach and record its limitations."},
            ],
        }
    return None


def _template_result(
    data: dict[str, str], sources: list[str], internal_links: list[str]
) -> dict:
    keyword = data["primary_keyword"].strip()
    question = data["target_question"].strip()
    seo_title = f"{question[:220]}".strip()
    meta = (
        f"A practical {CONTENT_TYPES[data['content_type']].lower()} for "
        f"{data['intended_audience'].strip()} about {keyword}. "
        "Review cited sources and verify current claims before publication."
    )[:320]
    return {
        "seo_title": seo_title[:250],
        "meta_description": meta,
        "slug": _slugify(question),
        "body_markdown": _template_body(data, sources, internal_links),
        "generation_mode": "template",
    }


def _llm_result(
    data: dict[str, str],
    sources: list[str],
    internal_links: list[str],
    *,
    api_key: str,
    api_url: str,
    model: str,
    timeout: float,
) -> dict:
    if not api_url.startswith("https://"):
        raise ContentGenerationError("The configured content API endpoint must use HTTPS.")
    prompt_data = {
        **data,
        "supporting_sources": sources,
        "internal_links": internal_links,
    }
    request_body = {
        "model": model,
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": (
                    "Create an editorial draft, not a factual authority. Return a JSON object with exactly "
                    "seo_title, meta_description, and body_markdown string fields. Use only supplied source URLs "
                    "as citations; never invent citations, product features, prices, or claims. Mark uncertain "
                    "product claims for verification. The opening two sentences should answer the target question "
                    "as far as the supplied evidence allows, and state uncertainty when it does not."
                ),
            },
            {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
        ],
    }
    request = Request(
        api_url,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with _open_without_redirects(
            request, timeout=min(max(float(timeout), 1.0), 30.0)
        ) as response:
            response_data = json.loads(response.read(1_000_000))
    except HTTPError as error:
        if error.code == 429:
            raise ContentGenerationError(
                "The content provider is rate limiting requests. Wait and retry or choose template mode."
            ) from error
        if error.code in {401, 403}:
            raise ContentGenerationError(
                "The configured content provider rejected its credentials. Check the server configuration."
            ) from error
        raise ContentGenerationError(
            f"The content provider returned HTTP {error.code}. Retry or choose template mode."
        ) from error
    except (TimeoutError, socket.timeout):
        raise ContentGenerationError(
            "The content provider timed out. Retry or choose template mode."
        ) from None
    except URLError:
        raise ContentGenerationError(
            "The content provider could not be reached. Retry or choose template mode."
        ) from None
    except OSError:
        raise ContentGenerationError(
            "The content provider request failed. Retry or choose template mode."
        ) from None
    except (json.JSONDecodeError, RecursionError, TypeError, UnicodeDecodeError, ValueError):
        raise ContentGenerationError(
            "The content provider returned malformed data. Retry or choose template mode."
        ) from None
    try:
        content = response_data["choices"][0]["message"]["content"]
        draft = json.loads(content)
        seo_title = draft["seo_title"]
        meta_description = draft["meta_description"]
        body = draft["body_markdown"]
        if not all(
            isinstance(item, str) and item.strip()
            for item in (seo_title, meta_description, body)
        ):
            raise ValueError
        seo_title = seo_title.strip()
        meta_description = meta_description.strip()
        body = body.strip()
    except (
        AttributeError,
        KeyError,
        IndexError,
        RecursionError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ):
        raise ContentGenerationError(
            "The content provider response did not match the required draft format. Retry or choose template mode."
        ) from None
    append = _source_markdown(sources, internal_links)
    if append:
        body += "\n\n" + append
    body += (
        "\n\n## Editorial verification note\n"
        "External LLM-assisted draft. Review every factual, product, pricing, and comparative claim and verify citations before publication."
    )
    return {
        "seo_title": seo_title[:250],
        "meta_description": meta_description[:320],
        "slug": _slugify(seo_title),
        "body_markdown": body[:50000],
        "generation_mode": "llm",
    }


def generate_content_draft(
    data: dict[str, str],
    *,
    api_key: str | None = None,
    api_url: str = "https://api.openai.com/v1/chat/completions",
    model: str = "gpt-4o-mini",
    timeout: float = 15,
) -> tuple[dict, str | None]:
    sources = _clean_urls(data.get("supporting_sources"))
    internal_links = _clean_urls(data.get("internal_links"))
    if data.get("generation_mode") == "llm" and api_key:
        draft = _llm_result(
            data,
            sources,
            internal_links,
            api_key=api_key,
            api_url=api_url,
            model=model,
            timeout=timeout,
        )
        return draft, None
    draft = _template_result(data, sources, internal_links)
    if data.get("generation_mode") == "llm":
        return draft, (
            "CONTENT_LLM_API_KEY is not configured. Saved using clearly labelled template-based drafting; "
            "no external AI model was used."
        )
    return draft, None


def build_quality_checklist(
    body_markdown: str,
    seo_title: str,
    meta_description: str,
    sources: list[str],
    links: list[str],
) -> list[dict]:
    intro = body_markdown.split("\n## ", 1)[0]
    intro = re.sub(r"^#.*\n+", "", intro, count=1)
    opening_sentences = re.findall(r"[^.!?]+[.!?](?:[\"'”’)]*)", intro)
    has_h1 = bool(re.search(r"(?m)^#\s+\S", body_markdown))
    has_h2 = bool(re.search(r"(?m)^##\s+\S", body_markdown))
    return [
        {
            "item": "Opening has two sentences within the first paragraph; confirm it answers the target question",
            "done": len(opening_sentences) >= 2,
        },
        {
            "item": "SEO title and meta description are present",
            "done": bool(seo_title.strip() and meta_description.strip()),
        },
        {"item": "H1 and H2 headings are present", "done": has_h1 and has_h2},
        {"item": "Supporting source URLs were supplied", "done": bool(sources)},
        {"item": "Internal links were supplied for editorial review", "done": bool(links)},
        {"item": "Product, price, availability, and comparison claims verified", "done": False},
        {"item": "All supplied sources opened and support the adjacent claims", "done": False},
        {"item": "Final human review and publication approval completed", "done": False},
    ]


def build_schema_markup(data: dict, seo_title: str, meta_description: str) -> dict | None:
    return _schema_for(data, seo_title, meta_description)
