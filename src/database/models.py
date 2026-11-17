"""SQLAlchemy ORM models for course_service."""

import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""


class Course(Base):
    """Top-level course entity."""

    __tablename__ = "courses"
    __table_args__ = (Index("idx_course_status", "status"),)

    course_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="planning", server_default="planning"
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_analysis: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    curriculum: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    progress_pct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_lessons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_lessons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    failed_lessons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    skipped_lessons: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bundle_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    modules: Mapped[list[Module]] = relationship(
        "Module", back_populates="course", lazy="selectin", cascade="all, delete-orphan"
    )
    build_logs: Mapped[list[CourseBuildLog]] = relationship(
        "CourseBuildLog", back_populates="course", lazy="noload", cascade="all, delete-orphan"
    )


class Module(Base):
    """Module within a course."""

    __tablename__ = "modules"
    __table_args__ = (Index("idx_module_course_position", "course_id", "position"),)

    module_id: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("courses.course_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    intro_video_job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    intro_video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    slide_deck_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    course: Mapped[Course] = relationship("Course", back_populates="modules")
    lessons: Mapped[list[Lesson]] = relationship(
        "Lesson",
        back_populates="module",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="[Lesson.module_id]",
    )


class Lesson(Base):
    """Lesson within a module, maps 1:1 to a pipeline job."""

    __tablename__ = "lessons"
    __table_args__ = (
        Index("idx_lesson_module_position", "module_id", "position"),
        Index("idx_lesson_course", "course_id"),
        Index("idx_lesson_pipeline_status", "pipeline_status"),
    )

    lesson_id: Mapped[str] = mapped_column(String, primary_key=True)
    module_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("modules.module_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("courses.course_id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_type: Mapped[str] = mapped_column(
        String, nullable=False, default="mixed", server_default="mixed"
    )
    target_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=120, server_default="120"
    )
    source_material_refs: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    lesson_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    pipeline_job_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    pipeline_status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending", server_default="pending"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    video_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifacts: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    module: Mapped[Module] = relationship("Module", back_populates="lessons")
    course: Mapped[Course] = relationship("Course", foreign_keys=[course_id])


class CourseBuildLog(Base):
    """Audit log for course build events."""

    __tablename__ = "course_build_logs"
    __table_args__ = (Index("idx_buildlog_course", "course_id"),)

    log_id: Mapped[str] = mapped_column(String, primary_key=True)
    course_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("courses.course_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    lesson_id: Mapped[str | None] = mapped_column(String, nullable=True)
    module_id: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    course: Mapped[Course] = relationship("Course", back_populates="build_logs")
