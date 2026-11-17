"""Initial migration: create all course_service tables.

Revision ID: 001_initial_tables
Revises:
Create Date: 2026-03-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001_initial_tables"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- courses (top-level entity, no FK dependencies) --
    op.create_table(
        "courses",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("course_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="planning"),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("source_config", JSONB, nullable=False),
        sa.Column("source_analysis", JSONB, nullable=True),
        sa.Column("curriculum", JSONB, nullable=True),
        sa.Column("manifest", JSONB, nullable=True),
        sa.Column("config", JSONB, nullable=False, server_default="{}"),
        sa.Column("progress_pct", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("skipped_lessons", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bundle_url", sa.String(2048), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- modules (FK → courses.course_id) --
    op.create_table(
        "modules",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("module_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column(
            "course_id",
            sa.String(36),
            sa.ForeignKey("courses.course_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("intro_video_job_id", sa.String(36), nullable=True),
        sa.Column("intro_video_url", sa.String(2048), nullable=True),
        sa.Column("slide_deck_path", sa.String(2048), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_module_course_position", "modules", ["course_id", "position"])

    # -- lessons (FK → modules.module_id, courses.course_id) --
    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("lesson_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column(
            "module_id",
            sa.String(36),
            sa.ForeignKey("modules.module_id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "course_id",
            sa.String(36),
            sa.ForeignKey("courses.course_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("topic", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("video_type", sa.String(30), nullable=False, server_default="mixed"),
        sa.Column("target_duration_seconds", sa.Integer, nullable=False, server_default="120"),
        sa.Column("source_material_refs", JSONB, nullable=True),
        sa.Column("lesson_config", JSONB, nullable=True),
        sa.Column("pipeline_job_id", sa.String(36), nullable=True, index=True),
        sa.Column("pipeline_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("video_url", sa.String(2048), nullable=True),
        sa.Column("artifacts", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_lesson_module_position", "lessons", ["module_id", "position"])
    op.create_index("idx_lesson_course", "lessons", ["course_id"])

    # -- course_build_logs (FK → courses.course_id) --
    op.create_table(
        "course_build_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("log_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column(
            "course_id",
            sa.String(36),
            sa.ForeignKey("courses.course_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("lesson_id", sa.String(36), nullable=True),
        sa.Column("module_id", sa.String(36), nullable=True),
        sa.Column("details", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("idx_buildlog_course", "course_build_logs", ["course_id"])


def downgrade() -> None:
    op.drop_table("course_build_logs")
    op.drop_table("lessons")
    op.drop_table("modules")
    op.drop_table("courses")
