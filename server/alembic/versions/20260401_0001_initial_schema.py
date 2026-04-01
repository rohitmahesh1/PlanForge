"""Initial schema

Revision ID: 20260401_0001
Revises:
Create Date: 2026-04-01 00:01:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260401_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("google_refresh_token_encrypted", sa.String(), nullable=True),
        sa.Column("default_calendar_id", sa.String(length=256), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "changelog",
        sa.Column("op_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("gcal_event_id", sa.String(length=128), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("op_id"),
    )
    op.create_index("ix_changelog_timestamp", "changelog", ["timestamp"], unique=False)
    op.create_index("ix_changelog_user_id", "changelog", ["user_id"], unique=False)

    op.create_table(
        "policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("json", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_policies_user_id", "policies", ["user_id"], unique=False)

    op.create_table(
        "prefs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("sleep_start", sa.String(length=8), nullable=False),
        sa.Column("sleep_end", sa.String(length=8), nullable=False),
        sa.Column("min_buffer_min", sa.Integer(), nullable=False),
        sa.Column("default_event_len_min", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prefs_user_id", "prefs", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_prefs_user_id", table_name="prefs")
    op.drop_table("prefs")

    op.drop_index("ix_policies_user_id", table_name="policies")
    op.drop_table("policies")

    op.drop_index("ix_changelog_user_id", table_name="changelog")
    op.drop_index("ix_changelog_timestamp", table_name="changelog")
    op.drop_table("changelog")

    op.drop_table("users")
