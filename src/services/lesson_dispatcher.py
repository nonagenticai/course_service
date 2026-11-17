"""Dispatch lesson pipeline jobs to the orchestrator with concurrency control."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from src.clients.orchestrator_client import OrchestratorClient
from src.database.models import Lesson
from src.database.repository import CourseRepository
from src.services.lesson_outcome import DEGRADED, normalize_lesson_status

logger = logging.getLogger(__name__)


class LessonDispatcher:
    """Dispatch and track lesson pipeline jobs."""

    def __init__(
        self,
        orchestrator_client: OrchestratorClient,
        repository: CourseRepository,
        concurrency_limit: int = 10,
        max_retries: int = 3,
        lesson_timeout: float = 1800.0,
        callback_base_url: str | None = None,
    ) -> None:
        self._client = orchestrator_client
        self._repo = repository
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._max_retries = max_retries
        self._lesson_timeout = lesson_timeout
        self._callback_base_url = callback_base_url

    async def dispatch_all(
        self,
        lessons: list[Lesson],
        source_analysis: dict[str, Any],
        course_id: str,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Dispatch all lessons concurrently with semaphore-bounded parallelism.

        Returns:
            Summary dict with completed, partial, failed, skipped counts.
            ``completed`` counts only lessons that produced a clean video;
            ``partial`` counts terminal-but-degraded lessons. A caller deciding
            whether the course delivered must not read ``completed`` alone.
        """
        logger.info(
            "LessonDispatcher: dispatching %d lessons for course %s (concurrency=%d)",
            len(lessons),
            course_id,
            self._semaphore._value,
        )

        tasks = [
            self._dispatch_single(lesson, source_analysis, course_id, user_context)
            for lesson in lessons
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        completed = 0
        partial = 0
        failed = 0
        skipped = 0
        for r in results:
            if isinstance(r, Exception):
                failed += 1
            elif r == "completed":
                completed += 1
            elif r == DEGRADED:
                partial += 1
            elif r == "skipped":
                skipped += 1
            else:
                failed += 1

        logger.info(
            "LessonDispatcher: course %s — completed=%d, partial=%d, failed=%d, skipped=%d",
            course_id,
            completed,
            partial,
            failed,
            skipped,
        )
        return {
            "completed": completed,
            "partial": partial,
            "failed": failed,
            "skipped": skipped,
        }

    async def _dispatch_single(
        self,
        lesson: Lesson,
        source_analysis: dict[str, Any],
        course_id: str,
        user_context: dict[str, Any] | None,
    ) -> str:
        """Dispatch a single lesson with retry logic."""
        async with self._semaphore:
            for attempt in range(self._max_retries + 1):
                try:
                    return await self._execute_lesson(
                        lesson, source_analysis, course_id, user_context
                    )
                except (OSError, RuntimeError, httpx.HTTPError, TimeoutError, ValueError) as exc:
                    logger.warning(
                        "Lesson %s attempt %d/%d failed: %s",
                        lesson.lesson_id,
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    await self._repo.update_lesson(
                        lesson.lesson_id,
                        retry_count=attempt + 1,
                        error=str(exc),
                    )
                    if attempt >= self._max_retries:
                        await self._repo.update_lesson(
                            lesson.lesson_id,
                            pipeline_status="skipped",
                        )
                        return "skipped"

            return "skipped"

    async def _execute_lesson(
        self,
        lesson: Lesson,
        source_analysis: dict[str, Any],
        course_id: str,
        user_context: dict[str, Any] | None,
    ) -> str:
        """Submit and wait for a single lesson pipeline."""
        callback_url = None
        if self._callback_base_url:
            callback_url = f"{self._callback_base_url}/api/courses/{course_id}/callback"

        target_duration = lesson.target_duration_seconds or 120

        lesson_context = {
            **(user_context or {}),
            "lesson_topic": lesson.topic or lesson.title,
            "lesson_title": lesson.title,
            "target_duration": target_duration,
        }

        result = await self._client.create_pipeline(
            mode="lesson",
            prior_results={"analyze": source_analysis},
            user_context=lesson_context,
            callback_url=callback_url,
            config_overrides={
                "target_duration_minutes": target_duration / 60,
            },
        )

        job_id = result.get("job_id", "")
        await self._repo.update_lesson(
            lesson.lesson_id,
            pipeline_job_id=job_id,
            pipeline_status="queued",
        )

        logger.info(
            "Lesson %s dispatched as pipeline job %s",
            lesson.lesson_id,
            job_id,
        )

        # Poll for completion (fallback if callback doesn't fire)
        try:
            poll_result = await self._client.wait_for_pipeline(job_id, timeout=self._lesson_timeout)
        except (TimeoutError, RuntimeError) as exc:
            await self._repo.update_lesson(
                lesson.lesson_id,
                pipeline_status="failed",
                error=str(exc),
            )
            raise

        video_url = poll_result.get("video_url")
        artifacts = poll_result.get("artifacts")
        raw_status = poll_result.get("status", "completed")

        # ``poll_job`` returns for completed / completed_with_errors / partial
        # alike. Returning the literal "completed" here reported every one of
        # them as a win; the caller then had no way to tell a delivered lesson
        # from one the orchestrator only half-produced.
        status = normalize_lesson_status(raw_status, video_url=video_url)

        update_fields: dict[str, Any] = {
            "pipeline_status": status,
            "video_url": video_url,
            "artifacts": artifacts,
        }
        if status == DEGRADED:
            reason = (
                f"pipeline reported {raw_status!r} without a video_url"
                if raw_status in ("completed",)
                else f"pipeline reported {raw_status!r}"
            )
            update_fields["error"] = reason
            logger.warning(
                "Lesson %s did not deliver a clean video: %s",
                lesson.lesson_id,
                reason,
            )

        await self._repo.update_lesson(lesson.lesson_id, **update_fields)

        return status

    async def dispatch_single_retry(
        self,
        lesson: Lesson,
        source_analysis: dict[str, Any],
        course_id: str,
        user_context: dict[str, Any] | None = None,
    ) -> str:
        """Retry a single failed lesson."""
        await self._repo.update_lesson(
            lesson.lesson_id,
            pipeline_status="pending",
            error=None,
        )
        return await self._dispatch_single(lesson, source_analysis, course_id, user_context)
