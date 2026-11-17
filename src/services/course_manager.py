"""Course lifecycle manager — coordinates planning, dispatch, and assembly.

Every public method that touches a course-by-id requires a ``tenant_id``
keyword argument and threads it down to the repository, which enforces the
tenant filter at the SQL ``WHERE`` clause. Internal pipeline methods are
called with the ``tenant_id`` already resolved by the entry-point method
(usually the route or the lifespan recovery loop).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast
from uuid import uuid7

import httpx
from sqlalchemy.exc import SQLAlchemyError

from src.clients.analysis_client import AnalysisClient
from src.clients.orchestrator_client import OrchestratorClient
from src.clients.slide_client import SlideClient
from src.database.models import Course, CourseBuildLog, Lesson, Module
from src.database.repository import CourseRepository
from src.services.bundle_assembler import BundleAssembler
from src.services.curriculum.planner import CurriculumPlanner
from src.services.lesson_dispatcher import LessonDispatcher
from src.services.lesson_outcome import (
    DEGRADED,
    DEGRADED_STATUSES,
    DELIVERED_STATUSES,
    TERMINAL_STATUSES,
    normalize_lesson_status,
)
from src.services.progress_tracker import ProgressTracker

logger = logging.getLogger(__name__)


class CourseManager:
    """Orchestrate the full course creation lifecycle."""

    def __init__(
        self,
        repository: CourseRepository,
        analysis_client: AnalysisClient,
        orchestrator_client: OrchestratorClient,
        planner: CurriculumPlanner,
        slide_client: SlideClient | None = None,
        concurrency_limit: int = 10,
        max_retries: int = 3,
        lesson_timeout: float = 1800.0,
        callback_base_url: str | None = None,
    ) -> None:
        self._repo = repository
        self._analysis_client = analysis_client
        self._orchestrator_client = orchestrator_client
        self._planner = planner
        self._slide_client = slide_client
        self._dispatcher = LessonDispatcher(
            orchestrator_client=orchestrator_client,
            repository=repository,
            concurrency_limit=concurrency_limit,
            max_retries=max_retries,
            lesson_timeout=lesson_timeout,
            callback_base_url=callback_base_url,
        )
        self._progress = ProgressTracker(repository)
        self._assembler = BundleAssembler(repository)

    # ------------------------------------------------------------------
    # Public accessor methods (used by API routes)
    # ------------------------------------------------------------------

    async def get_course(self, course_id: str, *, tenant_id: str) -> Course | None:
        """Return the course record for the caller's tenant, or None."""
        return await self._repo.get_course(course_id, tenant_id=tenant_id)

    async def list_courses(
        self, tenant_id: str, offset: int = 0, limit: int = 20
    ) -> tuple[list[Course], int]:
        """Return a page of courses and total count for the given tenant."""
        return await self._repo.list_courses(tenant_id, offset=offset, limit=limit)

    async def count_courses(self, tenant_id: str) -> int:
        """Return total course count for a tenant."""
        return await self._repo.count_courses(tenant_id)

    async def get_modules(self, course_id: str, *, tenant_id: str) -> list[Module]:
        """Return all modules for a course, scoped to tenant."""
        return await self._repo.get_modules(course_id, tenant_id=tenant_id)

    async def get_lessons(self, course_id: str, *, tenant_id: str) -> list[Lesson]:
        """Return all lessons for a course, scoped to tenant."""
        return await self._repo.get_lessons(course_id, tenant_id=tenant_id)

    async def get_module(self, module_id: str) -> Module | None:
        """Return a single module or None (internal — manager pipeline only)."""
        return await self._repo.get_module(module_id)

    async def get_lessons_for_module(self, module_id: str) -> list[Lesson]:
        """Return all lessons for a specific module (internal)."""
        return await self._repo.get_lessons_for_module(module_id)

    async def get_lesson(self, lesson_id: str) -> Lesson | None:
        """Return a single lesson or None (internal — manager pipeline only)."""
        return await self._repo.get_lesson(lesson_id)

    async def delete_course(self, course_id: str, *, tenant_id: str) -> bool:
        """Delete a course for the given tenant; returns True if it existed."""
        return await self._repo.delete_course(course_id, tenant_id=tenant_id)

    async def build_manifest(self, course_id: str, *, tenant_id: str) -> dict[str, Any]:
        """Build (or rebuild) the course manifest on demand (tenant-scoped)."""
        return await self._assembler.build_manifest(course_id, tenant_id=tenant_id)

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    async def create_course(
        self,
        *,
        tenant_id: str,
        source_type: str,
        source_url: str | None = None,
        source_documents: list[dict[str, Any]] | None = None,
        config: dict[str, Any] | None = None,
    ) -> Course:
        """Create a new course and start async processing."""
        course_id = str(uuid7())
        cfg = config or {}

        course = Course(
            course_id=course_id,
            tenant_id=tenant_id,
            status="planning",
            source_config={
                "source_type": source_type,
                "source_url": source_url,
                "source_documents": source_documents,
            },
            config=cfg,
        )
        course = await self._repo.create_course(course)
        await self._log_event(course_id, "course_created")

        logger.info("CourseManager: created course %s for tenant %s", course_id, tenant_id)
        return course

    async def run_pipeline(
        self,
        course_id: str,
        *,
        tenant_id: str,
        user_context: dict[str, Any] | None = None,
    ) -> None:
        """Run the full course pipeline: analyze -> plan -> dispatch -> assemble.

        This is the main async entrypoint called after course creation. The
        ``tenant_id`` MUST match the course's owning tenant; the underlying
        repo calls return None / zero rows on mismatch and the pipeline
        will fail cleanly with "Course not found".
        """
        try:
            course = await self._repo.get_course(course_id, tenant_id=tenant_id)
            if not course:
                raise ValueError(f"Course {course_id} not found")

            config = course.config or {}
            source_config = course.source_config or {}

            # Phase 1: Analysis
            await self._repo.update_course(course_id, tenant_id=tenant_id, status="analyzing")
            await self._log_event(course_id, "analysis_started")

            analysis = await self._run_analysis(source_config)
            await self._repo.update_course(course_id, tenant_id=tenant_id, source_analysis=analysis)
            await self._log_event(course_id, "analysis_completed")

            # Phase 2: Curriculum planning
            await self._repo.update_course(
                course_id, tenant_id=tenant_id, status="planning_curriculum"
            )
            await self._log_event(course_id, "curriculum_planning_started")

            curriculum = await self._planner.plan(
                analysis,
                max_lessons=config.get("max_lessons", 200),
                target_lesson_duration_seconds=config.get("target_lesson_duration_seconds", 120),
                user_context=user_context,
            )

            await self._repo.update_course(
                course_id,
                tenant_id=tenant_id,
                title=curriculum.get("title"),
                description=curriculum.get("description"),
                curriculum=curriculum,
                status="curriculum_ready",
            )
            await self._log_event(course_id, "curriculum_ready")

            # Persist modules and lessons
            _, lessons = await self._persist_curriculum(course_id, curriculum)

            await self._repo.update_course(
                course_id,
                tenant_id=tenant_id,
                total_lessons=len(lessons),
            )

            # Phase 3: Lesson dispatch
            await self._repo.update_course(course_id, tenant_id=tenant_id, status="producing")
            await self._log_event(course_id, "production_started")

            summary = await self._dispatcher.dispatch_all(
                lessons=lessons,
                source_analysis=analysis,
                course_id=course_id,
                user_context=user_context,
            )

            counts = await self._progress.update_progress(course_id, tenant_id=tenant_id)
            await self._update_module_statuses(course_id, tenant_id=tenant_id)
            await self._log_event(course_id, "production_completed", details={**summary, **counts})

            # Phase 4: Supplementary content (intros, slide decks)
            await self._repo.update_course(course_id, tenant_id=tenant_id, status="supplementary")
            await self._log_event(course_id, "supplementary_started")

            await self._generate_supplementary_content(
                course_id, config, analysis, tenant_id=tenant_id
            )

            await self._log_event(course_id, "supplementary_completed")

            # Phase 5: Assembly
            await self._repo.update_course(course_id, tenant_id=tenant_id, status="assembling")
            await self._log_event(course_id, "assembly_started")

            await self._assembler.build_manifest(course_id, tenant_id=tenant_id)

            # The dispatcher does NOT raise when a lesson exhausts its retries —
            # it marks the lesson "skipped" and returns normally. Writing
            # status="completed" unconditionally here therefore reported a
            # course where every lesson was lost as a clean success, with
            # error=None and a manifest full of null video_urls.
            verdict, verdict_error = self._delivery_verdict(counts)
            await self._repo.update_course(
                course_id,
                tenant_id=tenant_id,
                status=verdict,
                error=verdict_error,
            )
            await self._log_event(
                course_id,
                "course_completed" if verdict == "completed" else "course_incomplete",
                details=counts,
            )

            logger.info(
                "CourseManager: course %s finished with status=%s (%d/%d lessons delivered)",
                course_id,
                verdict,
                counts["completed"],
                counts["total"],
            )

        except (OSError, ValueError, RuntimeError, SQLAlchemyError, httpx.HTTPError) as exc:
            logger.error("CourseManager: course %s failed: %s", course_id, exc, exc_info=True)
            await self._repo.update_course(
                course_id,
                tenant_id=tenant_id,
                status="failed",
                error=str(exc),
            )
            await self._log_event(course_id, "course_failed", details={"error": str(exc)})
            raise

    async def _run_analysis(self, source_config: dict[str, Any]) -> dict[str, Any]:
        """Run analysis on source material.

        For web sources, uses the orchestrator's crawl+analyze stages
        (the analysis API expects pre-crawled pages, not raw URLs).
        """
        source_type = source_config.get("source_type", "web")
        source_url = source_config.get("source_url")

        if source_type == "web" and source_url:
            result = await self._orchestrator_client.create_pipeline(
                mode="full",
                url=source_url,
                config_overrides={"stages_enabled": ["crawl", "analyze"]},
            )
            job_id = result["job_id"]
            job = await self._orchestrator_client.wait_for_pipeline(job_id, timeout=600.0)
            return job.get("results", {}).get("analyze", {})
        if source_type == "document":
            documents = source_config.get("source_documents", [])
            return {
                "pages": documents,
                "raw": {"source_type": "document"},
            }
        raise ValueError(f"Unsupported source type: {source_type}")

    async def _persist_curriculum(
        self, course_id: str, curriculum: dict[str, Any]
    ) -> tuple[list[Module], list[Lesson]]:
        """Create module and lesson records from curriculum data."""
        modules: list[Module] = []
        lessons: list[Lesson] = []

        for i, mod_data in enumerate(curriculum.get("modules", [])):
            module_id = str(uuid7())
            module = Module(
                module_id=module_id,
                course_id=course_id,
                position=i + 1,
                title=mod_data.get("title", f"Module {i + 1}"),
                description=mod_data.get("description"),
            )
            modules.append(module)

            for j, lesson_data in enumerate(mod_data.get("lessons", [])):
                lesson_id = str(uuid7())
                lesson = Lesson(
                    lesson_id=lesson_id,
                    module_id=module_id,
                    course_id=course_id,
                    position=j + 1,
                    title=lesson_data.get("title", f"Lesson {j + 1}"),
                    topic=lesson_data.get("topic"),
                    description=lesson_data.get("description"),
                    video_type=lesson_data.get("video_type", "mixed"),
                    target_duration_seconds=lesson_data.get("target_duration_seconds", 120),
                )
                lessons.append(lesson)

        await self._repo.create_modules_bulk(modules)
        await self._repo.create_lessons_bulk(lessons)

        logger.info(
            "CourseManager: persisted %d modules, %d lessons for course %s",
            len(modules),
            len(lessons),
            course_id,
        )
        return modules, lessons

    async def handle_callback(
        self,
        course_id: str,
        *,
        tenant_id: str,
        job_id: str,
        status: str,
        video_url: str | None = None,
        error: str | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> bool:
        """Handle a pipeline callback for a lesson, scoped to the caller's tenant.

        Returns True if the callback was applied, False if the course does
        not belong to this tenant (the orchestrator forwards tenant_id on
        every S2S call; mismatched callbacks are dropped silently).
        """
        # Authorise: confirm the course belongs to the caller's tenant
        # before reading lessons (which would also be tenant-filtered).
        owner = await self._repo.get_course(course_id, tenant_id=tenant_id)
        if not owner:
            logger.warning(
                "CourseManager: callback for course %s rejected — tenant %s does not own it",
                course_id,
                tenant_id,
            )
            return False

        lessons = await self._repo.get_lessons(course_id, tenant_id=tenant_id)
        target = None
        for ln in lessons:
            if ln.pipeline_job_id == job_id:
                target = ln
                break

        if not target:
            logger.warning(
                "CourseManager: callback for unknown job %s in course %s",
                job_id,
                course_id,
            )
            return False

        # Same normalisation as the dispatcher's polling path: a callback
        # reporting "completed" with no video_url is a measurement gap, not a
        # delivery, and the two write paths must agree or the course verdict
        # depends on which one happened to win.
        update_fields: dict[str, Any] = {
            "pipeline_status": normalize_lesson_status(status, video_url=video_url)
        }
        if video_url:
            update_fields["video_url"] = video_url
        if error:
            update_fields["error"] = error
        if artifacts:
            update_fields["artifacts"] = artifacts

        await self._repo.update_lesson(target.lesson_id, **update_fields)
        await self._progress.update_progress(course_id, tenant_id=tenant_id)
        await self._update_module_statuses(course_id, tenant_id=tenant_id)

        logger.info(
            "CourseManager: callback processed for lesson %s (job %s) → %s",
            target.lesson_id,
            job_id,
            status,
        )
        return True

    async def retry_lesson(
        self,
        course_id: str,
        lesson_id: str,
        *,
        tenant_id: str,
        user_context: dict[str, Any] | None = None,
    ) -> str:
        """Retry a single failed lesson (tenant-scoped on the course)."""
        course = await self._repo.get_course(course_id, tenant_id=tenant_id)
        if not course:
            raise ValueError(f"Course {course_id} not found")

        lesson = await self._repo.get_lesson(lesson_id)
        if not lesson or lesson.course_id != course_id:
            raise ValueError(f"Lesson {lesson_id} not found")

        # A degraded lesson is a lost lesson; without this it would be an
        # unretryable dead end.
        if lesson.pipeline_status not in ("failed", "skipped", DEGRADED):
            raise ValueError(
                f"Lesson {lesson_id} is not retryable (status={lesson.pipeline_status})"
            )

        analysis = course.source_analysis or {}
        result = await self._dispatcher.dispatch_single_retry(
            lesson, analysis, course_id, user_context
        )
        await self._progress.update_progress(course_id, tenant_id=tenant_id)
        return result

    # ------------------------------------------------------------------
    # Supplementary content generation
    # ------------------------------------------------------------------

    async def _generate_supplementary_content(
        self,
        course_id: str,
        config: dict[str, Any],
        analysis: dict[str, Any],
        *,
        tenant_id: str,
    ) -> None:
        """Generate module intros, course intro/outro, and slide decks."""
        modules = await self._repo.get_modules(course_id, tenant_id=tenant_id)
        lessons = await self._repo.get_lessons(course_id, tenant_id=tenant_id)

        lessons_by_module: dict[str, list[Lesson]] = {}
        for ln in lessons:
            mid = str(ln.module_id)
            lessons_by_module.setdefault(mid, []).append(ln)

        tasks: list[asyncio.Task[None]] = []

        if config.get("generate_module_intros", True):
            for mod in modules:
                mod_lessons = lessons_by_module.get(str(mod.module_id), [])
                tasks.append(
                    asyncio.create_task(
                        self._generate_module_intro(mod, mod_lessons, analysis, course_id)
                    )
                )

        if config.get("generate_course_intro", True) and modules:
            tasks.append(
                asyncio.create_task(
                    self._generate_course_intro(course_id, modules, analysis, tenant_id=tenant_id)
                )
            )

        if config.get("generate_slide_decks", True) and self._slide_client:
            for mod in modules:
                mod_lessons = lessons_by_module.get(str(mod.module_id), [])
                tasks.append(
                    asyncio.create_task(self._generate_slide_deck(mod, mod_lessons, course_id))
                )

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        "Supplementary task %d failed for course %s: %s",
                        i,
                        course_id,
                        result,
                    )

    async def _generate_module_intro(
        self,
        module: Module,
        lessons: list[Lesson],
        analysis: dict[str, Any],
        course_id: str,
    ) -> None:
        """Generate an intro video for a module via the orchestrator."""
        lesson_titles = [ln.title for ln in sorted(lessons, key=lambda x: x.position)]

        intro_context = {
            "lesson_topic": f"Introduction to {module.title}",
            "lesson_title": f"{module.title} — Module Introduction",
            "target_duration": 60,
            "module_title": module.title,
            "module_description": module.description or "",
            "lesson_titles": lesson_titles,
            "is_module_intro": True,
        }

        try:
            result = await self._orchestrator_client.create_pipeline(
                mode="lesson",
                prior_results={"analyze": analysis},
                user_context=intro_context,
                config_overrides={"target_duration_minutes": 1.0},
            )
            job_id = result.get("job_id", "")
            await self._repo.update_module(module.module_id, intro_video_job_id=job_id)
            await self._log_event(
                course_id,
                "module_intro_dispatched",
                module_id=module.module_id,
                details={"job_id": job_id},
            )

            poll_result = await self._orchestrator_client.wait_for_pipeline(job_id, timeout=600.0)
            video_url = poll_result.get("video_url")
            # Deliberately does NOT write ``status``. A module's status is a
            # verdict about its lessons, owned by ``_update_module_statuses``.
            # This ran afterwards and used to overwrite that verdict with
            # "completed" — even when the intro itself returned no video_url —
            # so a module whose every lesson failed was rescued to "completed"
            # by its own 60-second intro.
            await self._repo.update_module(
                module.module_id,
                intro_video_url=video_url,
            )
            await self._log_event(
                course_id,
                "module_intro_completed",
                module_id=module.module_id,
            )
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning(
                "Module intro generation failed for %s: %s",
                module.module_id,
                exc,
            )
            await self._log_event(
                course_id,
                "module_intro_failed",
                module_id=module.module_id,
                details={"error": str(exc)},
            )

    async def _generate_course_intro(
        self,
        course_id: str,
        modules: list[Module],
        analysis: dict[str, Any],
        *,
        tenant_id: str,
    ) -> None:
        """Generate a course intro/outro video."""
        course = await self._repo.get_course(course_id, tenant_id=tenant_id)
        if not course:
            return

        module_titles = [m.title for m in sorted(modules, key=lambda x: x.position)]

        intro_context = {
            "lesson_topic": f"Welcome to {course.title or 'the course'}",
            "lesson_title": f"{course.title or 'Course'} — Introduction",
            "target_duration": 90,
            "course_title": course.title or "",
            "course_description": course.description or "",
            "module_titles": module_titles,
            "is_course_intro": True,
        }

        try:
            result = await self._orchestrator_client.create_pipeline(
                mode="lesson",
                prior_results={"analyze": analysis},
                user_context=intro_context,
                config_overrides={"target_duration_minutes": 1.5},
            )
            job_id = result.get("job_id", "")
            await self._log_event(
                course_id,
                "course_intro_dispatched",
                details={"job_id": job_id},
            )

            poll_result = await self._orchestrator_client.wait_for_pipeline(job_id, timeout=600.0)
            intro_video_url = poll_result.get("video_url")
            # Store intro video URL in the manifest config for assembly
            raw_cfg: dict[str, Any] = cast("dict[str, Any]", course.config) if course.config else {}
            existing_config: dict[str, Any] = {k: v for k, v in raw_cfg.items()}
            existing_config["intro_video_url"] = intro_video_url
            await self._repo.update_course(course_id, tenant_id=tenant_id, config=existing_config)
            await self._log_event(
                course_id,
                "course_intro_completed",
                details={"video_url": intro_video_url},
            )
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning("Course intro generation failed for %s: %s", course_id, exc)
            await self._log_event(
                course_id,
                "course_intro_failed",
                details={"error": str(exc)},
            )

    async def _generate_slide_deck(
        self,
        module: Module,
        lessons: list[Lesson],
        course_id: str,
    ) -> None:
        """Generate a slide deck for a module."""
        if not self._slide_client:
            return

        lesson_data = [
            {
                "title": ln.title,
                "topic": ln.topic or ln.title,
                "description": ln.description or "",
            }
            for ln in sorted(lessons, key=lambda x: x.position)
        ]

        try:
            job_id = await self._slide_client.create_slide_deck(
                module_title=module.title,
                lessons=lesson_data,
            )
            await self._log_event(
                course_id,
                "slide_deck_dispatched",
                module_id=module.module_id,
                details={"job_id": job_id},
            )

            result = await self._slide_client.wait_for_slide_deck(job_id, timeout=300.0)
            slide_path = result.get("pptx_path") or result.get("download_url")
            await self._repo.update_module(module.module_id, slide_deck_path=slide_path)
            await self._log_event(
                course_id,
                "slide_deck_completed",
                module_id=module.module_id,
            )
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning(
                "Slide deck generation failed for module %s: %s",
                module.module_id,
                exc,
            )
            await self._log_event(
                course_id,
                "slide_deck_failed",
                module_id=module.module_id,
                details={"error": str(exc)},
            )

    async def _update_module_statuses(self, course_id: str, *, tenant_id: str) -> None:
        """Update each module's status based on its lessons' statuses."""
        modules = await self._repo.get_modules(course_id, tenant_id=tenant_id)
        all_lessons = await self._repo.get_lessons(course_id, tenant_id=tenant_id)

        lessons_by_module: dict[str, list[Lesson]] = {}
        for ln in all_lessons:
            lessons_by_module.setdefault(str(ln.module_id), []).append(ln)

        for mod in modules:
            mod_lessons = lessons_by_module.get(str(mod.module_id), [])
            if not mod_lessons:
                continue
            statuses = {str(ln.pipeline_status) for ln in mod_lessons}
            # "partial" / "completed_with_errors" are terminal outcomes the
            # orchestrator really emits. They were missing from this set, so a
            # module holding one stayed "pending" forever underneath a course
            # that called itself completed.
            if not statuses.issubset(TERMINAL_STATUSES):
                continue  # still in progress
            mod_statuses = [str(ln.pipeline_status) for ln in mod_lessons]
            delivered = sum(1 for s in mod_statuses if s in DELIVERED_STATUSES)
            degraded = sum(1 for s in mod_statuses if s in DEGRADED_STATUSES)
            if delivered == len(mod_lessons):
                new_status = "completed"
            elif delivered > 0 or degraded > 0:
                new_status = "partial"
            else:
                new_status = "failed"
            await self._repo.update_module(str(mod.module_id), status=new_status)

    @staticmethod
    def _delivery_verdict(counts: dict[str, int]) -> tuple[str, str | None]:
        """Decide the course's final status from what its lessons delivered.

        The line between "failed" and "partial": **any loss is visible, but
        only total loss is a failure.** A course that delivered 9 of 10 lessons
        is a usable deliverable — calling that "failed" would be a false
        negative that invites the caller to discard real work — but it is not
        "completed" either, so it reports "partial" and the caller can read
        ``completed_lessons`` / ``total_lessons`` for the size of the loss. A
        course that delivered nothing has nothing usable in it and is a
        failure by any definition. No percentage threshold is invented,
        because any threshold here would be arbitrary.

        A course with no lessons at all planned (``total == 0``) is a planning
        outcome, not a delivery loss, and stays "completed".
        """
        total = counts.get("total", 0)
        delivered = counts.get("completed", 0)
        if total == 0 or delivered == total:
            return "completed", None
        lost = total - delivered
        if delivered == 0:
            return "failed", f"0 of {total} lessons produced a video"
        return "partial", f"{lost} of {total} lessons did not produce a video"

    async def _log_event(
        self,
        course_id: str,
        event_type: str,
        lesson_id: str | None = None,
        module_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write a build log event."""
        log = CourseBuildLog(
            log_id=str(uuid7()),
            course_id=course_id,
            event_type=event_type,
            lesson_id=lesson_id,
            module_id=module_id,
            details=details,
        )
        await self._repo.add_build_log(log)
