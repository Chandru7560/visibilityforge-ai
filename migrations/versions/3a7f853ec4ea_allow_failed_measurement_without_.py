"""enforce research evidence integrity

Revision ID: 3a7f853ec4ea
Revises: e6f87ed56577
Create Date: 2026-10-30 22:14:33.671174

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '3a7f853ec4ea'
down_revision = 'e6f87ed56577'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER ck_questions_verified_source_insert
            BEFORE INSERT ON questions
            WHEN NEW.verification_status = 'verified'
              AND (NEW.source_url IS NULL OR length(trim(NEW.source_url)) = 0)
            BEGIN
              SELECT RAISE(ABORT, 'verified questions require a source URL');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER ck_questions_verified_source_update
            BEFORE UPDATE OF verification_status, source_url ON questions
            WHEN NEW.verification_status = 'verified'
              AND (NEW.source_url IS NULL OR length(trim(NEW.source_url)) = 0)
            BEGIN
              SELECT RAISE(ABORT, 'verified questions require a source URL');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER ck_measurements_observed_answer_insert
            BEFORE INSERT ON measurements
            WHEN NEW.collection_status != 'failed'
              AND length(trim(COALESCE(NEW.raw_answer, ''))) = 0
            BEGIN
              SELECT RAISE(ABORT, 'observed answer is required unless collection failed');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER ck_measurements_observed_answer_update
            BEFORE UPDATE OF collection_status, raw_answer ON measurements
            WHEN NEW.collection_status != 'failed'
              AND length(trim(COALESCE(NEW.raw_answer, ''))) = 0
            BEGIN
              SELECT RAISE(ABORT, 'observed answer is required unless collection failed');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER ck_measurements_collection_error_insert
            BEFORE INSERT ON measurements
            WHEN NEW.collection_status != 'complete'
              AND length(trim(COALESCE(NEW.error_status, ''))) = 0
            BEGIN
              SELECT RAISE(ABORT, 'incomplete collections require an error status');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER ck_measurements_collection_error_update
            BEFORE UPDATE OF collection_status, error_status ON measurements
            WHEN NEW.collection_status != 'complete'
              AND length(trim(COALESCE(NEW.error_status, ''))) = 0
            BEGIN
              SELECT RAISE(ABORT, 'incomplete collections require an error status');
            END
            """
        )
        return

    op.create_check_constraint(
        'ck_measurements_observed_answer',
        'measurements',
        "collection_status = 'failed' OR length(trim(raw_answer)) > 0",
    )
    op.create_check_constraint(
        'ck_measurements_collection_error',
        'measurements',
        "collection_status = 'complete' OR "
        "(error_status IS NOT NULL AND length(trim(error_status)) > 0)",
    )
    op.create_check_constraint(
        'ck_questions_verified_source',
        'questions',
        "verification_status != 'verified' OR "
        "(source_url IS NOT NULL AND length(trim(source_url)) > 0)",
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        for trigger in (
            "ck_measurements_collection_error_update",
            "ck_measurements_collection_error_insert",
            "ck_measurements_observed_answer_update",
            "ck_measurements_observed_answer_insert",
            "ck_questions_verified_source_update",
            "ck_questions_verified_source_insert",
        ):
            op.execute(f"DROP TRIGGER {trigger}")
        return

    op.drop_constraint(
        'ck_questions_verified_source', 'questions', type_='check'
    )
    op.drop_constraint(
        'ck_measurements_collection_error', 'measurements', type_='check'
    )
    op.drop_constraint(
        'ck_measurements_observed_answer', 'measurements', type_='check'
    )
