"""Database repository for course CRUD operations.

Course-level methods (lookup by ``course_id``) REQUIRE a ``tenant_id`` keyword
argument; the tenant filter is enforced inside the SQL ``WHERE`` clause — a
wrong-tenant lookup returns None / a wrong-tenant UPDATE is a zero-row no-op,
so the route layer's 404 mapping cannot be bypassed.

Module-/lesson-level methods (lookup by ``module_id`` / ``lesson_id``) remain
tenant-agnostic at the repository layer — they are only reachable from inside
the CourseManager pipeline, which has already authenticated the course's
tenant on entry. If these methods become part of the public route surface in
future, they should be migrated to the course-id-then-join pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from src.database.models import Course, CourseBuildLog, Lesson, Module

logger = logging.getLogger(__name__)


class CourseRepository:
    """CRUD operations for courses, modules, and lessons."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Course operations
    # ------------------------------------------------------------------

    async def create_course(self, course: Course) -> Course:
        """Persist a new course record."""
        async with self._session_factory() as session:
            session.add(course)
            await session.commit()
            await session.refresh(course)
            return course

    async def get_course(self, course_id: str, *, tenant_id: str) -> Course | None:
        """Fetch a single course by ID, scoped to the caller's tenant."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Course).where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
            )
            return result.scalar_one_or_none()

    async def get_course_with_modules(self, course_id: str, *, tenant_id: str) -> Course | None:
        """Fetch a course with eagerly loaded modules and their lessons."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Course)
                .where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .options(
                    selectinload(Course.modules).selectinload(Module.lessons),
                )
            )
            return result.scalar_one_or_none()

    async def list_courses(
        self, tenant_id: str, limit: int = 20, offset: int = 0
    ) -> tuple[list[Course], int]:
        """List courses for a tenant with pagination.

        Returns:
            Tuple of (course list, total count).
        """
        async with self._session_factory() as session:
            query = (
                select(Course)
                .where(Course.tenant_id == tenant_id)
                .order_by(Course.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            count_query = (
                select(func.count()).select_from(Course).where(Course.tenant_id == tenant_id)
            )

            result = await session.execute(query)
            count_result = await session.execute(count_query)
            return list(result.scalars().all()), count_result.scalar_one()

    async def update_course(
        self, course_id: str, *, tenant_id: str, **kwargs: Any
    ) -> Course | None:
        """Update course fields by ID, scoped to tenant. Returns the updated row or None."""
        async with self._session_factory() as session:
            await session.execute(
                update(Course)
                .where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .values(**kwargs)
            )
            await session.commit()

            result = await session.execute(
                select(Course).where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
            )
            return result.scalar_one_or_none()

    async def delete_course(self, course_id: str, *, tenant_id: str) -> bool:
        """Delete a course (and cascading data) for the given tenant.

        Returns True if a row was deleted, False if the course did not exist
        for this tenant (true miss or cross-tenant access attempt).
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Course).where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
            )
            course = result.scalar_one_or_none()
            if not course:
                return False
            await session.delete(course)
            await session.commit()
            return True

    # ------------------------------------------------------------------
    # Module operations
    # ------------------------------------------------------------------

    async def create_modules(self, modules: list[Module]) -> list[Module]:
        """Bulk-create module records."""
        async with self._session_factory() as session:
            session.add_all(modules)
            await session.commit()
            for m in modules:
                await session.refresh(m)
            return modules

    # Backward-compatible alias used by existing callers
    create_modules_bulk = create_modules

    async def get_module(self, module_id: str) -> Module | None:
        """Fetch a single module by ID (internal — manager-pipeline only)."""
        async with self._session_factory() as session:
            result = await session.execute(select(Module).where(Module.module_id == module_id))
            return result.scalar_one_or_none()

    async def get_modules(self, course_id: str, *, tenant_id: str) -> list[Module]:
        """Return all modules for a course, ordered by position (tenant-scoped).

        Filtered via a join on Course so a wrong-tenant course_id yields an
        empty list rather than another tenant's modules.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(Module)
                .join(Course, Course.course_id == Module.course_id)
                .where(
                    Module.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .order_by(Module.position)
            )
            return list(result.scalars().all())

    async def update_module(self, module_id: str, **kwargs: Any) -> Module | None:
        """Update module fields (internal — manager-pipeline only)."""
        async with self._session_factory() as session:
            await session.execute(
                update(Module).where(Module.module_id == module_id).values(**kwargs)
            )
            await session.commit()

            result = await session.execute(select(Module).where(Module.module_id == module_id))
            return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Lesson operations
    # ------------------------------------------------------------------

    async def create_lessons(self, lessons: list[Lesson]) -> list[Lesson]:
        """Bulk-create lesson records."""
        async with self._session_factory() as session:
            session.add_all(lessons)
            await session.commit()
            for ln in lessons:
                await session.refresh(ln)
            return lessons

    # Backward-compatible alias used by existing callers
    create_lessons_bulk = create_lessons

    async def get_lesson(self, lesson_id: str) -> Lesson | None:
        """Fetch a single lesson by ID (internal — manager-pipeline only)."""
        async with self._session_factory() as session:
            result = await session.execute(select(Lesson).where(Lesson.lesson_id == lesson_id))
            return result.scalar_one_or_none()

    async def update_lesson(self, lesson_id: str, **kwargs: Any) -> Lesson | None:
        """Update lesson fields (internal — manager-pipeline only)."""
        async with self._session_factory() as session:
            await session.execute(
                update(Lesson).where(Lesson.lesson_id == lesson_id).values(**kwargs)
            )
            await session.commit()

            result = await session.execute(select(Lesson).where(Lesson.lesson_id == lesson_id))
            return result.scalar_one_or_none()

    async def get_lessons_by_course(self, course_id: str, *, tenant_id: str) -> list[Lesson]:
        """Return all lessons for a course, ordered by module then position (tenant-scoped)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Lesson)
                .join(Course, Course.course_id == Lesson.course_id)
                .where(
                    Lesson.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .order_by(Lesson.module_id, Lesson.position)
            )
            return list(result.scalars().all())

    # Backward-compatible alias used by existing callers (course_manager, bundle_assembler)
    get_lessons = get_lessons_by_course

    async def get_lessons_for_module(self, module_id: str) -> list[Lesson]:
        """Return all lessons for a specific module (internal — manager-pipeline only)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Lesson).where(Lesson.module_id == module_id).order_by(Lesson.position)
            )
            return list(result.scalars().all())

    async def get_lessons_by_status(
        self, course_id: str, status: str, *, tenant_id: str
    ) -> list[Lesson]:
        """Return lessons for a course filtered by pipeline_status (tenant-scoped)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(Lesson)
                .join(Course, Course.course_id == Lesson.course_id)
                .where(
                    Lesson.course_id == course_id,
                    Lesson.pipeline_status == status,
                    Course.tenant_id == tenant_id,
                )
                .order_by(Lesson.module_id, Lesson.position)
            )
            return list(result.scalars().all())

    async def increment_lesson_retry(self, lesson_id: str) -> None:
        """Atomically increment the retry_count for a lesson."""
        async with self._session_factory() as session:
            await session.execute(
                update(Lesson)
                .where(Lesson.lesson_id == lesson_id)
                .values(retry_count=Lesson.retry_count + 1)
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Build log operations
    # ------------------------------------------------------------------

    async def add_build_log(self, log: CourseBuildLog) -> CourseBuildLog:
        """Persist a build log event."""
        async with self._session_factory() as session:
            session.add(log)
            await session.commit()
            await session.refresh(log)
            return log

    async def get_build_logs(
        self, course_id: str, *, tenant_id: str, limit: int = 100
    ) -> list[CourseBuildLog]:
        """Return recent build logs for a course, newest first (tenant-scoped)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(CourseBuildLog)
                .join(Course, Course.course_id == CourseBuildLog.course_id)
                .where(
                    CourseBuildLog.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .order_by(CourseBuildLog.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Aggregate operations
    # ------------------------------------------------------------------

    async def update_course_progress(self, course_id: str, *, tenant_id: str) -> None:
        """Recalculate and persist course progress from lesson statuses (tenant-scoped).

        Counts completed, failed, and skipped lessons, computes progress_pct,
        and writes the totals back to the course record. The Course UPDATE is
        gated on ``tenant_id`` so a cross-tenant call is a zero-row no-op.
        """
        async with self._session_factory() as session:
            total_result = await session.execute(
                select(func.count()).select_from(Lesson).where(Lesson.course_id == course_id)
            )
            total = total_result.scalar_one()

            completed_result = await session.execute(
                select(func.count())
                .select_from(Lesson)
                .where(Lesson.course_id == course_id, Lesson.pipeline_status == "completed")
            )
            completed = completed_result.scalar_one()

            failed_result = await session.execute(
                select(func.count())
                .select_from(Lesson)
                .where(Lesson.course_id == course_id, Lesson.pipeline_status == "failed")
            )
            failed = failed_result.scalar_one()

            skipped_result = await session.execute(
                select(func.count())
                .select_from(Lesson)
                .where(Lesson.course_id == course_id, Lesson.pipeline_status == "skipped")
            )
            skipped = skipped_result.scalar_one()

            progress_pct = int((completed / total) * 100) if total > 0 else 0

            await session.execute(
                update(Course)
                .where(
                    Course.course_id == course_id,
                    Course.tenant_id == tenant_id,
                )
                .values(
                    total_lessons=total,
                    completed_lessons=completed,
                    failed_lessons=failed,
                    skipped_lessons=skipped,
                    progress_pct=progress_pct,
                )
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Convenience helpers (backward-compatible with existing callers)
    # ------------------------------------------------------------------

    async def count_courses(self, tenant_id: str) -> int:
        """Return total course count for a tenant."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(func.count()).select_from(Course).where(Course.tenant_id == tenant_id)
            )
            return result.scalar_one()

    async def list_incomplete_courses(self) -> list[Course]:
        """Return courses in non-terminal states (for startup recovery).

        Intentionally tenant-agnostic: this is called from the application
        lifespan for recovery of all in-flight pipelines after a restart and
        runs in a system context. Every callsite must already be running
        outside any request scope.
        """
        terminal = {"completed", "failed"}
        async with self._session_factory() as session:
            result = await session.execute(select(Course).where(Course.status.notin_(terminal)))
            return list(result.scalars().all())
