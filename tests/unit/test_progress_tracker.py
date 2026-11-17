"""Tests for progress_tracker module."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.database.models import Lesson
from src.services.progress_tracker import ProgressTracker


def _make_lesson(
    lesson_id: str = "lesson-001",
    pipeline_status: str = "pending",
) -> Lesson:
    """Create a Lesson instance inline without fixtures."""
    return Lesson(
        lesson_id=lesson_id,
        module_id="mod-001",
        course_id="course-001",
        position=1,
        title=f"Lesson {lesson_id}",
        target_duration_seconds=120,
        pipeline_status=pipeline_status,
    )


class TestComputeProgress:
    def test_all_completed(self) -> None:
        lessons = [
            _make_lesson("l1", "completed"),
            _make_lesson("l2", "completed"),
            _make_lesson("l3", "completed"),
        ]
        result = ProgressTracker.compute_progress(lessons)
        assert result["total"] == 3
        assert result["completed"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["progress_pct"] == 100

    def test_mixed_statuses(self) -> None:
        lessons = [
            _make_lesson("l1", "completed"),
            _make_lesson("l2", "failed"),
            _make_lesson("l3", "skipped"),
        ]
        result = ProgressTracker.compute_progress(lessons)
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert result["skipped"] == 1
        # progress_pct is the DELIVERED fraction. It used to read 100 here
        # because failures counted as progress.
        assert result["progress_pct"] == 33
        # "how far through the work are we" is still available, separately.
        assert result["terminal"] == 3

    def test_no_lessons(self) -> None:
        result = ProgressTracker.compute_progress([])
        assert result["total"] == 0
        assert result["completed"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["progress_pct"] == 0

    def test_all_pending(self) -> None:
        lessons = [
            _make_lesson("l1", "pending"),
            _make_lesson("l2", "pending"),
            _make_lesson("l3", "pending"),
        ]
        result = ProgressTracker.compute_progress(lessons)
        assert result["completed"] == 0
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["progress_pct"] == 0

    def test_all_skipped_is_zero_percent_delivered(self) -> None:
        """A course that delivered nothing must not read as 100% progress.

        This test previously asserted the opposite (100%), pinning the bug: a
        user watching a progress bar hit 100% on a course with no videos in it.
        """
        lessons = [
            _make_lesson("l1", "skipped"),
            _make_lesson("l2", "skipped"),
        ]
        result = ProgressTracker.compute_progress(lessons)
        assert result["skipped"] == 2
        assert result["completed"] == 0
        assert result["progress_pct"] == 0
        assert result["terminal"] == 2  # terminal, but nothing delivered

    def test_some_lessons_lost_is_distinguishable_from_none_lost(self) -> None:
        """A course that lost lessons must not look like one that lost none."""
        clean = ProgressTracker.compute_progress(
            [_make_lesson("l1", "completed"), _make_lesson("l2", "completed")]
        )
        lossy = ProgressTracker.compute_progress(
            [_make_lesson("l1", "completed"), _make_lesson("l2", "skipped")]
        )
        assert clean["progress_pct"] == 100
        assert lossy["progress_pct"] == 50
        assert clean["completed"] != lossy["completed"]

    def test_degraded_lesson_is_terminal_but_not_delivered(self) -> None:
        """`partial` / `completed_with_errors` are terminal but are not wins."""
        lessons = [
            _make_lesson("l1", "completed"),
            _make_lesson("l2", "partial"),
            _make_lesson("l3", "completed_with_errors"),
        ]
        result = ProgressTracker.compute_progress(lessons)
        assert result["completed"] == 1
        assert result["degraded"] == 2
        assert result["terminal"] == 3
        assert result["progress_pct"] == 33

    def test_single_lesson_completed(self) -> None:
        """Edge case: single lesson course, completed."""
        lessons = [_make_lesson("l1", "completed")]
        result = ProgressTracker.compute_progress(lessons)
        assert result["total"] == 1
        assert result["completed"] == 1
        assert result["progress_pct"] == 100

    def test_single_lesson_pending(self) -> None:
        """Edge case: single lesson course, still pending."""
        lessons = [_make_lesson("l1", "pending")]
        result = ProgressTracker.compute_progress(lessons)
        assert result["total"] == 1
        assert result["completed"] == 0
        assert result["progress_pct"] == 0


class TestUpdateProgress:
    async def test_update_progress_calls_repository(self) -> None:
        """Verify update_progress queries lessons and persists via update_course."""
        mock_repo = AsyncMock()
        mock_repo.get_lessons = AsyncMock(
            return_value=[
                _make_lesson("l1", "completed"),
                _make_lesson("l2", "failed"),
                _make_lesson("l3", "pending"),
            ]
        )
        mock_repo.update_course = AsyncMock()

        tracker = ProgressTracker(repository=mock_repo)
        result = await tracker.update_progress("course-001", tenant_id="t1")

        # Verify get_lessons was called with the correct course_id, tenant-scoped
        mock_repo.get_lessons.assert_awaited_once_with("course-001", tenant_id="t1")

        # Verify update_course was called with computed values
        mock_repo.update_course.assert_awaited_once_with(
            "course-001",
            tenant_id="t1",
            total_lessons=3,
            completed_lessons=1,
            failed_lessons=1,
            skipped_lessons=0,
            progress_pct=33,  # 1 delivered of 3; the failed lesson is not progress
        )

        # Verify returned dict
        assert result["total"] == 3
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert result["skipped"] == 0
        assert result["progress_pct"] == 33

    async def test_update_progress_empty_course(self) -> None:
        """Course with no lessons returns 0% progress."""
        mock_repo = AsyncMock()
        mock_repo.get_lessons = AsyncMock(return_value=[])
        mock_repo.update_course = AsyncMock()

        tracker = ProgressTracker(repository=mock_repo)
        result = await tracker.update_progress("course-empty", tenant_id="t1")

        mock_repo.update_course.assert_awaited_once_with(
            "course-empty",
            tenant_id="t1",
            total_lessons=0,
            completed_lessons=0,
            failed_lessons=0,
            skipped_lessons=0,
            progress_pct=0,
        )
        assert result["progress_pct"] == 0

    async def test_update_progress_all_skipped(self) -> None:
        """All-skipped lessons yield 0% progress — nothing was delivered."""
        mock_repo = AsyncMock()
        mock_repo.get_lessons = AsyncMock(
            return_value=[
                _make_lesson("l1", "skipped"),
                _make_lesson("l2", "skipped"),
            ]
        )
        mock_repo.update_course = AsyncMock()

        tracker = ProgressTracker(repository=mock_repo)
        result = await tracker.update_progress("course-skip", tenant_id="t1")

        assert result["progress_pct"] == 0
        assert result["skipped"] == 2
        assert result["completed"] == 0


class TestDegradedLessonsArePersisted:
    async def test_degraded_lessons_are_not_persisted_as_pending(self) -> None:
        """A terminal-but-degraded lesson must not fall out of every bucket.

        ``GET /courses/{id}/progress`` derives pending_lessons by subtracting
        completed + failed + skipped from total, so a lesson counted in none of
        them would be reported as still pending forever.
        """
        mock_repo = AsyncMock()
        mock_repo.get_lessons = AsyncMock(
            return_value=[
                _make_lesson("l1", "completed"),
                _make_lesson("l2", "partial"),
                _make_lesson("l3", "failed"),
                _make_lesson("l4", "skipped"),
            ]
        )
        mock_repo.update_course = AsyncMock()

        tracker = ProgressTracker(repository=mock_repo)
        await tracker.update_progress("course-001", tenant_id="t1")

        kwargs = mock_repo.update_course.call_args.kwargs
        pending = (
            kwargs["total_lessons"]
            - kwargs["completed_lessons"]
            - kwargs["failed_lessons"]
            - kwargs["skipped_lessons"]
        )
        assert pending == 0
        assert kwargs["completed_lessons"] == 1
        assert kwargs["failed_lessons"] == 2  # the real failure + the degraded one
        assert kwargs["progress_pct"] == 25
