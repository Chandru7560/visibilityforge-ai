from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.extensions import db
from app.models import Measurement, Question


def _is_cited(measurement: Measurement) -> bool:
    return bool(measurement.cited_urls)


def _is_assessed(measurement: Measurement) -> bool:
    return measurement.collection_status != "failed" and (
        measurement.mention_status in {"mentioned", "not_mentioned"}
        or measurement.brand_mentioned is not None
    )


def _is_mentioned(measurement: Measurement) -> bool:
    return measurement.mention_status == "mentioned" or (
        measurement.mention_status is None and measurement.brand_mentioned is True
    )


def calculate_visibility(
    user_id: int,
    *,
    market: str | None = None,
    engine: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    filters = [Measurement.user_id == user_id]
    if market:
        filters.append(Measurement.market == market)
    if engine:
        filters.append(Measurement.engine == engine)
    if start:
        filters.append(Measurement.collected_at >= start)
    if end:
        filters.append(Measurement.collected_at < end)

    observations = db.session.scalars(
        select(Measurement).where(*filters).order_by(Measurement.collected_at)
    ).all()
    assessed = [row for row in observations if _is_assessed(row)]
    mentioned = [row for row in assessed if _is_mentioned(row)]
    cited_mentions = [row for row in mentioned if _is_cited(row)]
    question_filters = [Question.user_id == user_id, Question.is_active.is_(True)]
    if market:
        question_filters.append(Question.market == market)
    total_questions = db.session.scalar(
        select(db.func.count(Question.id)).where(*question_filters)
    ) or 0
    active_question_ids = set(
        db.session.scalars(
            select(Question.id).where(
                *question_filters,
            )
        ).all()
    )
    evidence_question_ids = {
        row.question_id
        for row in observations
        if row.question_id in active_question_ids
        if row.evidence is not None and row.evidence not in ({}, [], "")
    }

    competitors: Counter[str] = Counter()
    for row in observations:
        if row.collection_status == "failed":
            continue
        for name in row.competitor_names or []:
            competitors[name] += 1

    grouped: dict[tuple[str, str], list[Measurement]] = {}
    for row in observations:
        grouped.setdefault((row.market, row.engine), []).append(row)
    breakdown = []
    for (group_market, group_engine), rows in sorted(grouped.items()):
        group_assessed = [
            row
            for row in rows
            if _is_assessed(row)
        ]
        group_mentions = sum(_is_mentioned(row) for row in group_assessed)
        group_citations = sum(
            _is_mentioned(row) and _is_cited(row)
            for row in group_assessed
        )
        breakdown.append(
            {
                "market": group_market,
                "engine": group_engine,
                "sample_size": len(rows),
                "assessed_sample": len(group_assessed),
                "question_coverage": len(
                    {r.question_id for r in rows if r.collection_status != "failed"}
                ),
                "mention_rate": _percent(group_mentions, len(group_assessed)),
                "citation_rate": _percent(group_citations, len(group_assessed)),
            }
        )

    periods = _period_change(assessed)
    return {
        "observation_count": len(observations),
        "assessed_sample": len(assessed),
        "mentioned_count": len(mentioned),
        "citation_count": len(cited_mentions),
        "mention_rate": _percent(len(mentioned), len(assessed)),
        "citation_rate": _percent(len(cited_mentions), len(assessed)),
        "total_questions": total_questions,
        "evidence_questions": len(evidence_question_ids),
        "evidence_coverage": _percent(len(evidence_question_ids), total_questions),
        "competitors": competitors.most_common(10),
        "breakdown": breakdown,
        "period_change": periods,
    }


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator * 100 / denominator, 1)


def _period_change(rows: list[Measurement]) -> dict[str, Any]:
    if not rows:
        return {
            "earlier": None,
            "recent": None,
            "change": None,
            "earlier_sample": 0,
            "recent_sample": 0,
        }
    ordered = sorted(rows, key=lambda row: row.collected_at)
    midpoint = len(ordered) // 2
    earlier = ordered[:midpoint]
    recent = ordered[midpoint:]
    if not earlier or not recent:
        return {
            "earlier": None,
            "recent": None,
            "change": None,
            "earlier_sample": len(earlier),
            "recent_sample": len(recent),
        }

    def rate(sample: list[Measurement]) -> float | None:
        mentions = sum(
            _is_mentioned(row)
            for row in sample
        )
        return _percent(mentions, len(sample))

    earlier_rate, recent_rate = rate(earlier), rate(recent)
    return {
        "earlier": earlier_rate,
        "recent": recent_rate,
        "change": round(recent_rate - earlier_rate, 1)
        if earlier_rate is not None and recent_rate is not None
        else None,
        "earlier_sample": len(earlier),
        "recent_sample": len(recent),
    }
