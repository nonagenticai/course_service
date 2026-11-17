"""Track and compute course progress from lesson statuses."""

from __future__ import annotations

import logging

from src.database.models import Lesson
from src.database.repository import CourseRepository
from src.services.lesson_outcome import (
    DEGRADED_STATUSES,
    DELIVERED_STATUSES,
    LOST_STATUSES,
    TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)


def _counts(lessons: list[Lesson]) -> dict[str, int]:
    """Bucket lessons by delivery outcome.

    ``progress_pct`` is the *delivered* fraction, not the *terminal* fraction.
    It used to be ``(completed + failed + skipped) / total``, which read 100%
    for a course where every single lesson was lost — the worst possible
    reading, because it is indistinguishable from a course that delivered
    everything. ``completed / total`` is also what
    ``CourseRepository.update_course_progress`` has always computed, so this
    removes a disagreement between the service's two implementations rather
    than inventing a third.

    "How far through the work are we" is still derivable: ``terminal`` is
    returned alongside, and every persisted count needed to compute it
    (``total``, ``completed``, ``failed``, ``skipped``) is on the course row.
    """
    total = len(lessons)
    statuses = [str(ln.pipeline_status) for ln in lessons]

    completed = sum(1 for s in statuses if s in DELIVERED_STATUSES)
    degraded = sum(1 for s in statuses if s in DEGRADED_STATUSES)
    failed = sum(1 for s in statuses if s == "failed")
    skipped = sum(1 for s in statuses if s == "skipped")
    terminal = sum(1 for s in statuses if s in TERMINAL_STATUSES)
    lost = sum(1 for s in statuses if s in LOST_STATUSES)

    return {
        "total": total,
        "completed": completed,
        "degraded": degraded,
        "failed": failed,
        "skipped": skipped,
        "lost": lost,
        "terminal": terminal,
        "progress_pct": int((completed / total) * 100) if total > 0 else 0,
    }


class ProgressTracker:
    """Compute and persist course progress metrics."""

    def __init__(self, repository: CourseRepository) -> None:
        self._repo = repository

    async def update_progress(self, course_id: str, *, tenant_id: str) -> dict[str, int]:
        """Recompute progress from lesson statuses and persist to course."""
        lessons = await self._repo.get_lessons(course_id, tenant_id=tenant_id)
        counts = _counts(lessons)

        # The course row has no column for degraded lessons, and
        # ``GET /api/courses/{id}/progress`` derives pending_lessons as
        # total - completed - failed - skipped. Leaving degraded lessons out
        # of every bucket would report them as "pending" forever, which is the
        # same fail-open this change exists to remove. At course level
        # ``failed_lessons`` is the delivery ledger's "did not produce a clean
        # video, and was not skipped" bucket; the lesson row itself still
        # carries the finer "partial" status for detail views.
        await self._repo.update_course(
            course_id,
            tenant_id=tenant_id,
            total_lessons=counts["total"],
            completed_lessons=counts["completed"],
            failed_lessons=counts["failed"] + counts["degraded"],
            skipped_lessons=counts["skipped"],
            progress_pct=counts["progress_pct"],
        )

        return counts

    @staticmethod
    def compute_progress(lessons: list[Lesson]) -> dict[str, int]:
        """Compute progress metrics without persisting."""
        return _counts(lessons)
