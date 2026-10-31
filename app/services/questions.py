import csv
import io
import re
from typing import Any

from sqlalchemy import select

from app.extensions import db
from app.models import Question


QUESTION_CSV_FIELDS = (
    "question",
    "topic",
    "source_url",
    "source_type",
    "market",
    "language",
    "verification_status",
)


class QuestionImportError(ValueError):
    pass


def normalize_question(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def find_duplicate(user_id: int, question_text: str, *, exclude_id: int | None = None):
    normalized = normalize_question(question_text)
    query = select(Question).where(Question.user_id == user_id)
    if exclude_id is not None:
        query = query.where(Question.id != exclude_id)
    return next(
        (
            question
            for question in db.session.scalars(query)
            if normalize_question(question.question_text) == normalized
        ),
        None,
    )


def parse_questions_csv(content: str, user_id: int) -> list[Question]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise QuestionImportError("The CSV file must include a header row.")
    if not set(QUESTION_CSV_FIELDS).issubset(reader.fieldnames):
        required = ", ".join(QUESTION_CSV_FIELDS)
        raise QuestionImportError(f"CSV headers must include: {required}.")
    rows: list[Question] = []
    seen: set[str] = set()
    existing = {
        normalize_question(item.question_text)
        for item in db.session.scalars(
            select(Question).where(Question.user_id == user_id)
        )
    }
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise QuestionImportError(
                f"Row {line_number}: unexpected extra columns; no rows were imported."
            )
        if not any((value or "").strip() for value in row.values()):
            continue
        if len(rows) >= 1000:
            raise QuestionImportError("A single import may contain at most 1,000 questions.")
        question_text = (row.get("question") or "").strip()
        normalized = normalize_question(question_text)
        if not normalized:
            raise QuestionImportError(f"Row {line_number}: question is required.")
        if normalized in existing or normalized in seen:
            raise QuestionImportError(
                f"Row {line_number}: duplicate question detected; no rows were imported."
            )
        item = Question(
            user_id=user_id,
            question_text=question_text,
            topic=(row.get("topic") or "").strip() or None,
            source_url=(row.get("source_url") or "").strip() or None,
            source_type=(row.get("source_type") or "").strip() or None,
            market=(row.get("market") or "").strip() or "US",
            language=(row.get("language") or "").strip() or "en",
            verification_status=(row.get("verification_status") or "").strip()
            or "unverified",
        )
        try:
            if item.verification_status == "verified" and not item.source_url:
                raise ValueError("A source URL is required to verify a question.")
        except ValueError as error:
            raise QuestionImportError(f"Row {line_number}: {error}") from error
        rows.append(item)
        seen.add(normalized)
    if not rows:
        raise QuestionImportError("The CSV file does not contain any questions.")
    return rows


def question_export_rows(questions: list[Question]) -> list[dict[str, Any]]:
    def safe(value: str) -> str:
        if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            return "'" + value
        return value

    return [
        {
            "question": safe(item.question_text),
            "topic": safe(item.topic or ""),
            "source_url": safe(item.source_url or ""),
            "source_type": safe(item.source_type or ""),
            "market": safe(item.market),
            "language": safe(item.language),
            "verification_status": safe(item.verification_status),
        }
        for item in questions
    ]
