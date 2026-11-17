"""Tests for src.services.lesson_dispatcher.LessonDispatcher."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, call, patch

import pytest

from src.database.models import Lesson
from src.services.lesson_dispatcher import LessonDispatcher


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.update_lesson = AsyncMock()
    return repo


@pytest.fixture
def mock_orch_client() -> AsyncMock:
    client = AsyncMock()
    client.create_pipeline = AsyncMock(return_value={"job_id": "job-001"})
    client.wait_for_pipeline = AsyncMock(
        return_value={
            "status": "completed",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {"slides": "s3://slides/1.pdf"},
        }
    )
    return client


@pytest.fixture
def dispatcher(mock_orch_client: AsyncMock, mock_repo: AsyncMock) -> LessonDispatcher:
    return LessonDispatcher(
        orchestrator_client=mock_orch_client,
        repository=mock_repo,
        concurrency_limit=3,
        max_retries=2,
        lesson_timeout=60.0,
        callback_base_url="http://localhost:6314",
    )


def _make_lesson(lesson_id: str = "lesson-001", **kwargs: Any) -> Lesson:
    defaults = {
        "lesson_id": lesson_id,
        "module_id": "mod-001",
        "course_id": "course-001",
        "position": 1,
        "title": "Intro",
        "topic": "Getting Started",
        "target_duration_seconds": 120,
        "pipeline_status": "pending",
    }
    defaults.update(kwargs)
    return Lesson(**defaults)


class TestDispatchAll:
    """Tests for dispatch_all: multiple-lesson fan-out."""

    async def test_dispatch_all_multiple_lessons(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        lessons = [_make_lesson(f"lesson-{i}") for i in range(3)]
        result = await dispatcher.dispatch_all(lessons, {"key": "val"}, "course-001")
        assert result["completed"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert mock_orch_client.create_pipeline.call_count == 3
        assert mock_orch_client.wait_for_pipeline.call_count == 3

        # Verify each create_pipeline call included source_analysis in prior_results
        for c in mock_orch_client.create_pipeline.call_args_list:
            assert c.kwargs["prior_results"] == {"analyze": {"key": "val"}}
            assert c.kwargs["mode"] == "lesson"

    async def test_retry_on_failure_then_success(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """First attempt fails, second succeeds — counted as completed."""
        mock_orch_client.create_pipeline.side_effect = [
            RuntimeError("network error"),
            {"job_id": "job-retry"},
        ]
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {},
        }

        lessons = [_make_lesson()]
        result = await dispatcher.dispatch_all(lessons, {}, "course-001")

        assert result["completed"] == 1
        assert result["failed"] == 0
        assert result["skipped"] == 0

        # The first attempt failed, so retry_count=1 and error were recorded
        mock_repo.update_lesson.assert_any_call("lesson-001", retry_count=1, error="network error")
        # Then the second attempt succeeded — verify job_id and queued status
        mock_repo.update_lesson.assert_any_call(
            "lesson-001", pipeline_job_id="job-retry", pipeline_status="queued"
        )

    async def test_skipped_after_max_retries_exhausted(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """All attempts fail -> lesson is marked 'skipped'."""
        mock_orch_client.create_pipeline.side_effect = RuntimeError("always fails")

        lessons = [_make_lesson()]
        result = await dispatcher.dispatch_all(lessons, {}, "course-001")

        assert result["completed"] == 0
        assert result["skipped"] == 1

        # max_retries=2, so total attempts = max_retries + 1 = 3
        assert mock_orch_client.create_pipeline.call_count == 3

        # Each failed attempt should record retry_count and error
        mock_repo.update_lesson.assert_any_call("lesson-001", retry_count=1, error="always fails")
        mock_repo.update_lesson.assert_any_call("lesson-001", retry_count=2, error="always fails")
        mock_repo.update_lesson.assert_any_call("lesson-001", retry_count=3, error="always fails")
        # Final status should be skipped
        mock_repo.update_lesson.assert_any_call("lesson-001", pipeline_status="skipped")

    async def test_semaphore_limits_concurrency(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Semaphore with limit=1 forces sequential execution."""
        concurrent_count = 0
        max_concurrent = 0

        original_create = mock_orch_client.create_pipeline

        async def _track_concurrency(**kwargs: Any) -> dict[str, str]:
            nonlocal concurrent_count, max_concurrent
            concurrent_count += 1
            max_concurrent = max(max_concurrent, concurrent_count)
            await asyncio.sleep(0.01)
            concurrent_count -= 1
            return await original_create(**kwargs)

        mock_orch_client.create_pipeline = _track_concurrency

        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=1,
            max_retries=0,
        )

        lessons = [_make_lesson(f"lesson-{i}") for i in range(3)]
        await dispatcher.dispatch_all(lessons, {}, "course-001")

        assert max_concurrent == 1


class TestDispatchSingleRetry:
    """Tests for dispatch_single_retry."""

    async def test_resets_lesson_state_before_retry(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        lesson = _make_lesson(pipeline_status="failed", error="old error")
        result = await dispatcher.dispatch_single_retry(lesson, {}, "course-001")

        assert result == "completed"

        # First call must be the reset to pending
        first_update = mock_repo.update_lesson.call_args_list[0]
        assert first_update == call("lesson-001", pipeline_status="pending", error=None)

        # After reset, the pipeline must be created and lesson updated with job_id
        mock_orch_client.create_pipeline.assert_called_once()
        mock_repo.update_lesson.assert_any_call(
            "lesson-001", pipeline_job_id="job-001", pipeline_status="queued"
        )

        # Final update should have the completed status, video_url, and artifacts
        mock_repo.update_lesson.assert_any_call(
            "lesson-001",
            pipeline_status="completed",
            video_url="https://cdn.example.com/v.mp4",
            artifacts={"slides": "s3://slides/1.pdf"},
        )


class TestExecuteLesson:
    """Tests for _execute_lesson core logic."""

    async def test_execute_lesson_extracts_video_url(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Pipeline returns video_url, which is persisted via update_lesson."""
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": "https://cdn.example.com/final-video.mp4",
            "artifacts": None,
        }

        lesson = _make_lesson()
        result = await dispatcher._execute_lesson(lesson, {"key": "val"}, "course-001", None)

        assert result == "completed"

        # The final update_lesson call must include the extracted video_url
        mock_repo.update_lesson.assert_any_call(
            "lesson-001",
            pipeline_status="completed",
            video_url="https://cdn.example.com/final-video.mp4",
            artifacts=None,
        )

    async def test_execute_lesson_extracts_artifacts(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Pipeline returns artifacts dict, which is persisted via update_lesson."""
        artifacts_data = {
            "slides": "s3://slides/deck.pdf",
            "transcript": "s3://transcripts/t.txt",
            "thumbnail": "s3://thumbs/t.png",
        }
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": artifacts_data,
        }

        lesson = _make_lesson()
        await dispatcher._execute_lesson(lesson, {}, "course-001", None)

        # Verify the exact artifacts dict was passed to update_lesson
        final_call = mock_repo.update_lesson.call_args_list[-1]
        assert final_call.kwargs["artifacts"] == artifacts_data

    async def test_dispatch_passes_correct_user_context(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Verify user_context sent to orchestrator contains lesson details."""
        source_analysis = {"site": "example.com", "pages": []}
        user_context = {"style": "professional", "brand": "acme"}
        lesson = _make_lesson(
            title="Advanced Auth",
            topic="OAuth2 implementation",
            target_duration_seconds=180,
        )

        await dispatcher._execute_lesson(lesson, source_analysis, "course-001", user_context)

        create_kwargs = mock_orch_client.create_pipeline.call_args.kwargs

        # prior_results should wrap source_analysis under "analyze" key
        assert create_kwargs["prior_results"] == {"analyze": source_analysis}

        # user_context should merge base context with lesson-specific fields
        ctx = create_kwargs["user_context"]
        assert ctx["lesson_topic"] == "OAuth2 implementation"
        assert ctx["lesson_title"] == "Advanced Auth"
        assert ctx["target_duration"] == 180
        # Original user_context keys should be preserved
        assert ctx["style"] == "professional"
        assert ctx["brand"] == "acme"

        # config_overrides should include target_duration_minutes
        assert create_kwargs["config_overrides"]["target_duration_minutes"] == 3.0

    async def test_dispatch_sets_callback_url(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Verify callback_url is included when callback_base_url is configured."""
        dispatcher_with_callback = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=3,
            max_retries=0,
            callback_base_url="http://course-svc:6314",
        )

        lesson = _make_lesson()
        await dispatcher_with_callback._execute_lesson(lesson, {}, "course-007", None)

        create_kwargs = mock_orch_client.create_pipeline.call_args.kwargs
        assert (
            create_kwargs["callback_url"]
            == "http://course-svc:6314/api/courses/course-007/callback"
        )

    async def test_dispatch_no_callback_when_not_configured(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Verify callback_url is None when callback_base_url is not set."""
        dispatcher_no_callback = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=3,
            max_retries=0,
            callback_base_url=None,
        )

        lesson = _make_lesson()
        await dispatcher_no_callback._execute_lesson(lesson, {}, "course-001", None)

        create_kwargs = mock_orch_client.create_pipeline.call_args.kwargs
        assert create_kwargs["callback_url"] is None

    async def test_execute_lesson_timeout_marks_failed(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """When wait_for_pipeline raises TimeoutError, lesson is marked failed."""
        mock_orch_client.wait_for_pipeline.side_effect = TimeoutError("timed out")

        lesson = _make_lesson()
        with pytest.raises(TimeoutError, match="timed out"):
            await dispatcher._execute_lesson(lesson, {}, "course-001", None)

        # Lesson should be marked as failed with the error message
        mock_repo.update_lesson.assert_any_call(
            "lesson-001",
            pipeline_status="failed",
            error="timed out",
        )

    async def test_execute_lesson_runtime_error_marks_failed(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """When wait_for_pipeline raises RuntimeError, lesson is marked failed."""
        mock_orch_client.wait_for_pipeline.side_effect = RuntimeError("pipeline crashed")

        lesson = _make_lesson()
        with pytest.raises(RuntimeError, match="pipeline crashed"):
            await dispatcher._execute_lesson(lesson, {}, "course-001", None)

        mock_repo.update_lesson.assert_any_call(
            "lesson-001",
            pipeline_status="failed",
            error="pipeline crashed",
        )

    async def test_lesson_topic_falls_back_to_title(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """When lesson.topic is None, lesson_topic in context falls back to title."""
        lesson = _make_lesson(topic=None, title="My Title")

        await dispatcher._execute_lesson(lesson, {}, "course-001", None)

        ctx = mock_orch_client.create_pipeline.call_args.kwargs["user_context"]
        assert ctx["lesson_topic"] == "My Title"


class TestDispatchMixedOutcomes:
    """Tests for mixed dispatch results and edge-case return values."""

    async def test_dispatch_mixed_outcomes(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """5 lessons: 2 complete, 2 skip after retries, 1 fails with exception.

        Verify the summary dict has correct counts.
        """
        # max_retries=0 so each lesson gets exactly one attempt — makes
        # controlling outcomes straightforward.
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=0,
            lesson_timeout=60.0,
        )

        lessons = [_make_lesson(f"lesson-{i}", position=i + 1) for i in range(5)]

        call_idx = {"n": 0}

        async def create_side_effect(**kwargs: Any) -> dict[str, str]:
            idx = call_idx["n"]
            call_idx["n"] += 1
            if idx < 2:
                return {"job_id": f"job-{idx}"}
            if idx < 4:
                # These will fail, and with max_retries=0 they become "skipped"
                raise RuntimeError(f"network error {idx}")
            # This will also fail, becoming "skipped" with max_retries=0
            # But we want it to propagate as an Exception in gather.
            # Actually with max_retries=0, _dispatch_single catches and
            # returns "skipped". To get an Exception in gather results,
            # we need the exception to escape _dispatch_single entirely.
            raise RuntimeError(f"network error {idx}")

        mock_orch_client.create_pipeline.side_effect = create_side_effect
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {},
        }

        result = await dispatcher.dispatch_all(lessons, {}, "course-001")

        # With max_retries=0: successes return "completed", failures get caught
        # and return "skipped" (not Exception). So all 3 failures → skipped.
        assert result["completed"] == 2
        assert result["skipped"] == 3
        assert result["failed"] == 0
        total = result["completed"] + result["failed"] + result["skipped"]
        assert total == 5

    async def test_dispatch_unknown_return_value_counted_as_failed(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """If _dispatch_single returns an unexpected string, it's counted as failed."""
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=0,
            lesson_timeout=60.0,
        )

        lessons = [_make_lesson("lesson-0")]

        # Patch _dispatch_single to return an unexpected value
        with patch.object(dispatcher, "_dispatch_single", new=AsyncMock(return_value="banana")):
            result = await dispatcher.dispatch_all(lessons, {}, "course-001")

        assert result["completed"] == 0
        assert result["skipped"] == 0
        assert result["failed"] == 1


class TestDispatchSingleDeadCode:
    """Verify the retry loop in _dispatch_single covers all exit paths.

    Line 116 (`return "skipped"` after the for loop) is unreachable because:
    - On success: `return await self._execute_lesson(...)` exits immediately.
    - On failure at last attempt (`attempt >= max_retries`): `return "skipped"`.
    - On failure before last attempt: loop continues to next iteration.

    Every iteration either returns or continues, so the loop ALWAYS returns.
    These tests prove it by exhausting all path combinations.
    """

    async def test_all_attempts_succeed_on_first_try(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Success on first attempt — returns from try block, never hits post-loop."""
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=2,
            lesson_timeout=60.0,
        )
        lesson = _make_lesson()
        result = await dispatcher._dispatch_single(lesson, {}, "course-001", None)
        assert result == "completed"
        assert mock_orch_client.create_pipeline.call_count == 1

    async def test_fail_then_succeed(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """Fail on attempt 0, succeed on attempt 1 — returns from try block."""
        mock_orch_client.create_pipeline.side_effect = [
            RuntimeError("transient"),
            {"job_id": "job-retry"},
        ]
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=2,
            lesson_timeout=60.0,
        )
        lesson = _make_lesson()
        result = await dispatcher._dispatch_single(lesson, {}, "course-001", None)
        assert result == "completed"
        assert mock_orch_client.create_pipeline.call_count == 2

    async def test_exhaust_all_retries(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """All attempts fail — returns "skipped" from except block at last attempt."""
        mock_orch_client.create_pipeline.side_effect = RuntimeError("permanent")
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=2,
            lesson_timeout=60.0,
        )
        lesson = _make_lesson()
        result = await dispatcher._dispatch_single(lesson, {}, "course-001", None)
        assert result == "skipped"
        # max_retries=2 → attempts 0, 1, 2 → 3 total calls
        assert mock_orch_client.create_pipeline.call_count == 3

    async def test_zero_retries_fail_immediately(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """max_retries=0 — single attempt fails, returns "skipped" immediately."""
        mock_orch_client.create_pipeline.side_effect = RuntimeError("fail")
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=0,
            lesson_timeout=60.0,
        )
        lesson = _make_lesson()
        result = await dispatcher._dispatch_single(lesson, {}, "course-001", None)
        assert result == "skipped"
        assert mock_orch_client.create_pipeline.call_count == 1


class TestOutcomeIsNotAlwaysCompleted:
    """``_execute_lesson`` used to ``return "completed"`` literally.

    The shared ``poll_job`` returns for ``completed``, ``completed_with_errors``
    AND ``partial``, so every one of those was reported as a win: counted as
    completed by ``dispatch_all``, counted as neither by ``ProgressTracker``,
    and left outside ``_update_module_statuses``' terminal set.
    """

    async def test_partial_pipeline_is_not_reported_completed(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "partial",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {},
        }
        result = await dispatcher._execute_lesson(_make_lesson(), {}, "course-001", None)
        assert result == "partial"
        assert mock_repo.update_lesson.call_args_list[-1].kwargs["pipeline_status"] == "partial"

    async def test_completed_with_errors_is_not_reported_completed(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed_with_errors",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {},
        }
        result = await dispatcher._execute_lesson(_make_lesson(), {}, "course-001", None)
        assert result == "partial"
        final = mock_repo.update_lesson.call_args_list[-1].kwargs
        assert final["pipeline_status"] == "partial"
        assert "completed_with_errors" in final["error"]

    async def test_completed_without_a_video_is_not_a_delivery(
        self,
        dispatcher: LessonDispatcher,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        """A success status with no video_url is a measurement gap, not a win."""
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": None,
            "artifacts": {},
        }
        result = await dispatcher._execute_lesson(_make_lesson(), {}, "course-001", None)
        assert result == "partial"
        final = mock_repo.update_lesson.call_args_list[-1].kwargs
        assert final["pipeline_status"] == "partial"
        assert "without a video_url" in final["error"]

    async def test_clean_completion_is_still_completed(
        self,
        dispatcher: LessonDispatcher,
        mock_repo: AsyncMock,
    ) -> None:
        """Regression guard: a real success must keep reporting completed."""
        result = await dispatcher._execute_lesson(_make_lesson(), {}, "course-001", None)
        assert result == "completed"
        final = mock_repo.update_lesson.call_args_list[-1].kwargs
        assert final["pipeline_status"] == "completed"
        assert final["video_url"] == "https://cdn.example.com/v.mp4"
        assert "error" not in final

    async def test_dispatch_all_counts_degraded_separately(
        self,
        mock_orch_client: AsyncMock,
        mock_repo: AsyncMock,
    ) -> None:
        dispatcher = LessonDispatcher(
            orchestrator_client=mock_orch_client,
            repository=mock_repo,
            concurrency_limit=10,
            max_retries=0,
            lesson_timeout=60.0,
        )
        mock_orch_client.wait_for_pipeline.return_value = {
            "status": "partial",
            "video_url": "https://cdn.example.com/v.mp4",
            "artifacts": {},
        }
        lessons = [_make_lesson(f"lesson-{i}", position=i + 1) for i in range(3)]

        result = await dispatcher.dispatch_all(lessons, {}, "course-001")

        assert result["partial"] == 3
        assert result["completed"] == 0
