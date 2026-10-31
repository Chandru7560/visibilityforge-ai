from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import (
    AuditIssue,
    AuditRun,
    CitationTarget,
    ContentPage,
    GrowthTask,
    Measurement,
    Question,
    WeeklyReport,
)
from app.services.visibility import _is_assessed, _is_mentioned


def _utc_boundary(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


def _in_period(start: date, end: date, field):
    return (field >= _utc_boundary(start), field < _utc_boundary(end + timedelta(days=1)))


def _rate(numerator: int, denominator: int) -> float | None:
    if not denominator:
        return None
    return round(100 * numerator / denominator, 1)


def _metric(
    value: Any,
    formula: str,
    sample_size: int,
    evidence: list[dict[str, Any]],
    numerator: int | None = None,
    denominator: int | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "formula": formula,
        "sample_size": sample_size,
        "numerator": numerator,
        "denominator": denominator,
        "evidence": evidence,
    }


def _evidence(record_type: str, record_id: int, label: str, href: str) -> dict[str, Any]:
    return {
        "record_type": record_type,
        "record_id": record_id,
        "label": label,
        "url": href,
    }


def collect_period_metrics(user_id: int, start: date, end: date) -> dict[str, Any]:
    question_filters = [
        Question.user_id == user_id,
        *_in_period(start, end, Question.created_at),
    ]
    questions = db.session.scalars(
        select(Question).where(*question_filters).order_by(Question.id)
    ).all()
    verified_questions = [
        item
        for item in questions
        if item.verification_status == "verified" and bool(item.source_url)
    ]

    measurement_filters = [
        Measurement.user_id == user_id,
        *_in_period(start, end, Measurement.collected_at),
    ]
    measurements = db.session.scalars(
        select(Measurement)
        .where(*measurement_filters)
        .order_by(Measurement.collected_at, Measurement.id)
    ).all()
    valid_measurements = [
        item
        for item in measurements
        if _is_assessed(item)
    ]
    mentioned = [item for item in valid_measurements if _is_mentioned(item)]
    cited = [item for item in mentioned if bool(item.cited_urls)]

    question_evidence = [
        _evidence(
            "question",
            item.id,
            f"Question #{item.id}: {item.question_text}",
            f"/research/questions/{item.id}",
        )
        for item in questions
    ]
    verified_evidence = [
        _evidence(
            "question_source",
            item.id,
            f"Verified source for question #{item.id}",
            f"/research/questions/{item.id}",
        )
        for item in verified_questions
    ]
    measurement_evidence = [
        _evidence(
            "measurement",
            item.id,
            f"{item.engine} · {item.market} · measurement #{item.id}",
            f"/research/measurements/{item.id}",
        )
        for item in measurements
    ]
    valid_evidence = [
        _evidence(
            "measurement",
            item.id,
            f"Assessed {item.engine} · {item.market} · measurement #{item.id}",
            f"/research/measurements/{item.id}",
        )
        for item in valid_measurements
    ]
    mentioned_evidence = [
        _evidence(
            "measurement",
            item.id,
            f"Observed IncuBrix mention · measurement #{item.id}",
            f"/research/measurements/{item.id}",
        )
        for item in mentioned
    ]
    cited_evidence = [
        _evidence(
            "measurement",
            item.id,
            f"Observed mention with citation · measurement #{item.id}",
            f"/research/measurements/{item.id}",
        )
        for item in cited
    ]

    coverage: dict[str, dict[str, Any]] = {}
    for measurement in measurements:
        key = f"{measurement.engine} · {measurement.market}"
        entry = coverage.setdefault(
            key,
            {
                "engine": measurement.engine,
                "market": measurement.market,
                "measurement_count": 0,
                "question_ids": set(),
                "failed_count": 0,
                "evidence": [],
            },
        )
        entry["measurement_count"] += 1
        entry["question_ids"].add(measurement.question_id)
        if measurement.collection_status == "failed":
            entry["failed_count"] += 1
        entry["evidence"].append(
            _evidence(
                "measurement",
                measurement.id,
                f"Measurement #{measurement.id}",
                f"/research/measurements/{measurement.id}",
            )
        )
    coverage_rows = []
    for _key, item in sorted(coverage.items()):
        question_ids = item.pop("question_ids")
        coverage_rows.append({**item, "question_count": len(question_ids)})

    competitor_counter: Counter[str] = Counter()
    competitor_evidence: dict[str, list[dict[str, Any]]] = {}
    for measurement in measurements:
        if measurement.collection_status == "failed":
            continue
        for raw_name in set(measurement.competitor_names or []):
            name = raw_name.strip()
            if not name:
                continue
            competitor_counter[name] += 1
            competitor_evidence.setdefault(name, []).append(
                _evidence(
                    "measurement",
                    measurement.id,
                    f"Competitor {name} recorded · measurement #{measurement.id}",
                    f"/research/measurements/{measurement.id}",
                )
            )
    competitors = [
        {
            "name": name,
            "frequency": count,
            "formula": "Count of non-failed measurements recording this competitor.",
            "sample_size": sum(
                1
                for row in measurements
                if row.collection_status != "failed"
                and name in (row.competitor_names or [])
            ),
            "evidence": competitor_evidence[name],
        }
        for name, count in competitor_counter.most_common()
    ]

    audit_filters = [
        AuditIssue.user_id == user_id,
        AuditRun.audit_type == "technical",
        *_in_period(start, end, AuditIssue.created_at),
    ]
    issues = db.session.scalars(
        select(AuditIssue)
        .join(AuditRun, AuditRun.id == AuditIssue.audit_run_id)
        .where(*audit_filters, AuditRun.user_id == user_id)
        .order_by(AuditIssue.id)
    ).all()
    issue_counts = {
        severity: [item for item in issues if item.severity == severity]
        for severity in ("critical", "high", "medium", "low")
    }
    issue_metrics = {
        severity: _metric(
            len(items),
            "Count of audit issues created in the selected period.",
            len(items),
            [
                _evidence(
                    "audit_issue",
                    item.id,
                    f"{item.severity.title()} · {item.title}",
                    f"/audits/issues/{item.id}",
                )
                for item in items
            ],
        )
        for severity, items in issue_counts.items()
    }

    content_filters = [
        ContentPage.user_id == user_id,
        *_in_period(start, end, ContentPage.created_at),
    ]
    content_created = db.session.scalars(
        select(ContentPage).where(*content_filters).order_by(ContentPage.id)
    ).all()
    published_filters = [
        ContentPage.user_id == user_id,
        ContentPage.status == "published",
        *_in_period(start, end, ContentPage.updated_at),
    ]
    content_published = db.session.scalars(
        select(ContentPage).where(*published_filters).order_by(ContentPage.id)
    ).all()
    content_evidence = [
        _evidence(
            "content_page",
            item.id,
            f"Content draft #{item.id}: {item.title}",
            f"/content/{item.id}",
        )
        for item in content_created
    ]

    citation_filters = [
        CitationTarget.user_id == user_id,
        *_in_period(start, end, CitationTarget.updated_at),
    ]
    citation_updates = db.session.scalars(
        select(CitationTarget).where(*citation_filters).order_by(CitationTarget.id)
    ).all()
    researched_citations = [
        item for item in citation_updates if item.research_status == "researched"
    ]

    failed_measurements = [
        item for item in measurements if item.collection_status == "failed"
    ]
    failed_runs = db.session.scalars(
        select(AuditRun)
        .where(
            AuditRun.user_id == user_id,
            AuditRun.status == "failed",
            *_in_period(start, end, AuditRun.started_at),
        )
        .order_by(AuditRun.id)
    ).all()
    failure_evidence = [
        _evidence(
            "measurement",
            item.id,
            f"Failed {item.engine} collection · measurement #{item.id}",
            f"/research/measurements/{item.id}",
        )
        for item in failed_measurements
    ] + [
        _evidence(
            "audit_run",
            item.id,
            f"Failed technical audit #{item.id}",
            f"/audits/{item.id}",
        )
        for item in failed_runs
    ]

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "metrics": {
            "questions_tracked": _metric(
                len(questions),
                "Count of question records created during the selected period.",
                len(questions),
                question_evidence,
            ),
            "verified_question_sources": _metric(
                len(verified_questions),
                "Count of period-created questions marked verified with a non-empty source URL.",
                len(questions),
                verified_evidence,
                len(verified_questions),
                len(questions),
            ),
            "measurements": _metric(
                len(measurements),
                "Count of measurement records collected during the selected period; includes incomplete and failed attempts.",
                len(measurements),
                measurement_evidence,
            ),
            "measurement_coverage": {
                "value": coverage_rows,
                "formula": "Group period measurements by engine and market; question_count is distinct questions with a recorded attempt.",
                "sample_size": len(measurements),
                "evidence": measurement_evidence,
            },
            "mention_rate": _metric(
                _rate(len(mentioned), len(valid_measurements)),
                "Mentioned valid measurements / valid assessed measurements × 100; includes legacy boolean assessments and excludes failed or unassessed checks.",
                len(valid_measurements),
                mentioned_evidence + valid_evidence,
                len(mentioned),
                len(valid_measurements),
            ),
            "citation_rate": _metric(
                _rate(len(cited), len(valid_measurements)),
                "Assessed measurements marked mentioned with ≥1 cited URL / all valid assessed measurements × 100; includes legacy boolean assessments.",
                len(valid_measurements),
                cited_evidence + valid_evidence,
                len(cited),
                len(valid_measurements),
            ),
            "competitor_mentions": {
                "value": competitors,
                "formula": "Frequency is the number of non-failed measurements in which each competitor was recorded.",
                "sample_size": len(
                    [item for item in measurements if item.collection_status != "failed"]
                ),
                "evidence": [
                    record for group in competitor_evidence.values() for record in group
                ],
            },
            "technical_issues_by_severity": {
                "value": issue_metrics,
                "formula": "Count of recorded technical audit issues by severity and creation date.",
                "sample_size": len(issues),
                "evidence": [
                    record
                    for metric in issue_metrics.values()
                    for record in metric["evidence"]
                ],
            },
            "content_drafts_created": _metric(
                len(content_created),
                "Count of content page records created in the selected period, regardless of current status.",
                len(content_created),
                content_evidence,
            ),
            "content_pages_marked_published": _metric(
                len(content_published),
                "Content records currently marked published in the workspace and updated during the selected period; this does not prove external publication.",
                len(content_published),
                [
                    _evidence(
                        "content_page",
                        item.id,
                        f"Marked published in workspace · {item.title}",
                        f"/content/{item.id}",
                    )
                    for item in content_published
                ],
                len(content_published),
                len(content_published),
            ),
            "citation_opportunities_researched": _metric(
                len(researched_citations),
                "Count of opportunities currently marked researched and updated during the selected period.",
                len(citation_updates),
                [
                    _evidence(
                        "citation_opportunity",
                        item.id,
                        f"Researched opportunity · {item.source_name or item.domain}",
                        f"/citations/{item.id}",
                    )
                    for item in researched_citations
                ],
            ),
            "data_collection_failures": {
                "value": len(failed_measurements) + len(failed_runs),
                "formula": "Failed measurement records plus failed technical audit runs started in the selected period.",
                "sample_size": len(measurements) + len(failed_runs),
                "evidence": failure_evidence,
            },
        },
        "evidence_index": [
            {
                "claim": claim,
                "record_count": len(metric.get("evidence", [])),
                "records": metric.get("evidence", []),
            }
            for claim, metric in _iter_metric_claims(
                {
                    "questions_tracked": _metric(
                        len(questions), "", len(questions), question_evidence
                    ),
                    "verified_question_sources": _metric(
                        len(verified_questions), "", len(questions), verified_evidence
                    ),
                    "measurements": _metric(
                        len(measurements), "", len(measurements), measurement_evidence
                    ),
                    "measurement_coverage": {
                        "evidence": measurement_evidence,
                    },
                },
                {
                    "mention_rate": {
                        "evidence": mentioned_evidence + valid_evidence
                    },
                    "citation_rate": {"evidence": cited_evidence + valid_evidence},
                    "competitor_mentions": {
                        "evidence": [
                            record for group in competitor_evidence.values() for record in group
                        ]
                    },
                    "technical_issues_by_severity": {
                        "evidence": [
                            record
                            for metric in issue_metrics.values()
                            for record in metric["evidence"]
                        ]
                    },
                    "content_drafts_created": {"evidence": content_evidence},
                    "content_pages_marked_published": {
                        "evidence": [
                            record
                            for item in content_published
                            for record in [
                                _evidence(
                                    "content_page",
                                    item.id,
                                    f"Marked published in workspace · {item.title}",
                                    f"/content/{item.id}",
                                )
                            ]
                        ]
                    },
                    "citation_opportunities_researched": {
                        "evidence": [
                            _evidence(
                                "citation_opportunity",
                                item.id,
                                f"Researched opportunity · {item.source_name or item.domain}",
                                f"/citations/{item.id}",
                            )
                            for item in researched_citations
                        ]
                    },
                    "data_collection_failures": {"evidence": failure_evidence},
                },
            )
        ],
    }


def _iter_metric_claims(*groups: dict[str, Any]):
    for group in groups:
        for name, metric in group.items():
            yield name, metric


def compare_periods(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    comparisons: dict[str, Any] = {}
    for name in (
        "questions_tracked",
        "verified_question_sources",
        "measurements",
        "mention_rate",
        "citation_rate",
        "content_drafts_created",
        "content_pages_marked_published",
        "citation_opportunities_researched",
        "data_collection_failures",
    ):
        current_metric = current["metrics"][name]
        previous_metric = previous["metrics"][name]
        current_value = current_metric["value"]
        previous_value = previous_metric["value"]
        comparable = bool(
            current_metric["sample_size"] or previous_metric["sample_size"]
        )
        if not comparable or current_value is None or previous_value is None:
            delta = None
        else:
            delta = round(current_value - previous_value, 1)
        comparisons[name] = {
            "current": current_value,
            "previous": previous_value,
            "change": delta,
            "unit": "percentage_points"
            if name in {"mention_rate", "citation_rate"}
            else "count",
            "current_sample_size": current_metric["sample_size"],
            "previous_sample_size": previous_metric["sample_size"],
            "comparable": comparable,
            "interpretation": "Descriptive period comparison only; it does not establish cause or SEO improvement.",
        }
    return comparisons


def build_report(user_id: int, start: date, end: date) -> dict[str, Any]:
    current = collect_period_metrics(user_id, start, end)
    duration_days = (end - start).days + 1
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=duration_days - 1)
    previous = collect_period_metrics(user_id, previous_start, previous_end)
    current["previous_period"] = previous["period"]
    current["period_comparison"] = compare_periods(current, previous)
    current["metadata"] = {
        "comparison_period_available": any(
            item["comparable"] for item in current["period_comparison"].values()
        ),
        "comparison_note": "Equal-length immediately preceding period; descriptive only, not causal.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    return current


def run_report_record(user_id: int, start: date, end: date) -> WeeklyReport:
    report_key = f"range:{start.isoformat()}:{end.isoformat()}"
    report = db.session.scalar(
        select(WeeklyReport).where(
            WeeklyReport.user_id == user_id,
            WeeklyReport.report_key == report_key,
        )
    )
    if report is None:
        report = db.session.scalar(
            select(WeeklyReport).where(
                WeeklyReport.user_id == user_id,
                WeeklyReport.week_start == start,
                WeeklyReport.report_key.is_(None),
            )
        )
        if report is None:
            report = WeeklyReport(
                user_id=user_id,
                week_start=start,
                period_end=end,
                report_key=report_key,
                summary=f"Evidence report for {start.isoformat()} through {end.isoformat()}",
                execution_status="running",
            )
            db.session.add(report)
        else:
            report.report_key = report_key
            report.period_end = end
            report.execution_status = "running"
    else:
        if report.execution_status == "running":
            return report
        report.week_start = start
        report.period_end = end
        report.execution_status = "running"
        report.error_message = None
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        report = db.session.scalar(
            select(WeeklyReport).where(
                WeeklyReport.user_id == user_id,
                WeeklyReport.report_key == report_key,
            )
        )
        if report is None or report.execution_status != "running":
            raise
        return report

    timer = perf_counter()
    try:
        report_data = build_report(user_id, start, end)
        report.metrics = report_data
        report.execution_status = "completed"
        report.error_message = None
        report.summary = (
            f"Evidence report for {start.isoformat()} through {end.isoformat()}: "
            f"{report_data['metrics']['measurements']['value']} measurements; "
            f"{report_data['metrics']['questions_tracked']['value']} questions created."
        )
    except (SQLAlchemyError, ValueError, TypeError, KeyError, OverflowError) as error:
        db.session.rollback()
        report = db.session.scalar(
            select(WeeklyReport).where(
                WeeklyReport.user_id == user_id,
                WeeklyReport.report_key == report_key,
            )
        )
        if report is None:
            raise
        report.execution_status = "failed"
        report.error_message = (
            f"Report generation failed ({type(error).__name__}). Review stored data and retry."
        )
        report.execution_duration_ms = round((perf_counter() - timer) * 1000)
        db.session.commit()
        raise
    report.execution_duration_ms = round((perf_counter() - timer) * 1000)
    db.session.commit()
    return report
