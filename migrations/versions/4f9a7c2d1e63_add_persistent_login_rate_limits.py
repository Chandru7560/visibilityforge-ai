"""Add persistent login rate-limit buckets.

Revision ID: 4f9a7c2d1e63
Revises: 08c51e4010ae
Create Date: 2026-10-30 23:20:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "4f9a7c2d1e63"
down_revision = "08c51e4010ae"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "login_attempts",
        sa.Column("bucket_key", sa.String(length=64), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_login_attempts_count_nonnegative",
        ),
        sa.PrimaryKeyConstraint("bucket_key"),
    )
    op.create_index(
        "ix_login_attempts_window_started",
        "login_attempts",
        ["window_started_at"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_login_attempts_window_started",
        table_name="login_attempts",
    )
    op.drop_table("login_attempts")
