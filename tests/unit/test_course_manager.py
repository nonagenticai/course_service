"""Tests for course_manager module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.database.models import Course, Lesson, Module
from src.services.course_manager import CourseManager
from src.services.curriculum.planner import CurriculumPlanner


@pytest.fixture
def mock_planner() -> AsyncMock:
    planner = AsyncMock(spec=CurriculumPlanner)
    planner.plan = AsyncMock(
        return_value={
            "title": "Test Course",
            "description": "A test course",
            "modules": [
                {
                    "title": "Module 1",
                    "description": "First module",
                    "lessons": [
                        {
                            "title": "Lesson 1",
                            "topic": "Topic 1",
                            "description": "First lesson",
                            "video_type": "mixed",
                            "target_duration_seconds": 120,
                        },
                    ],
                },
            ],
            "warnings": [],
        }
    )
    return planner


@pytest.fixture
def course_manager(
    mock_repository: AsyncMock,
    mock_analysis_client: AsyncMock,
    mock_orchestrator_client: AsyncMock,
    mock_planner: AsyncMock,
) -> CourseManager:
    return CourseManager(
        repository=mock_repository,
        analysis_client=mock_analysis_client,
        orchestrator_client=mock_orchestrator_client,
        planner=mock_planner,
        concurrency_limit=2,
        max_retries=1,
        lesson_timeout=60.0,
    )


class TestCreateCourse:
    async def test_creates_course_record(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.create_course.return_value = Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={},
        )

        course = await course_manager.create_course(
            tenant_id="t1",
            source_type="web",
            source_url="https://example.com",
        )

        # Verify the returned course has correct values
        assert course.status == "planning"
        assert course.tenant_id == "t1"

        # Verify the Course object passed to create_course
        mock_repository.create_course.assert_called_once()
        created_course = mock_repository.create_course.call_args[0][0]
        assert isinstance(created_course, Course)
        assert created_course.tenant_id == "t1"
        assert created_course.status == "planning"
        assert created_course.source_config == {
            "source_type": "web",
            "source_url": "https://example.com",
            "source_documents": None,
        }
        assert created_course.config == {}

        # Verify build log was written for course_created event
        mock_repository.add_build_log.assert_called_once()
        build_log = mock_repository.add_build_log.call_args[0][0]
        assert build_log.event_type == "course_created"

    async def test_creates_course_with_documents(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        docs = [{"path": "/tmp/doc.pdf", "format": "pdf"}]
        mock_repository.create_course.return_value = Course(
            course_id="c2",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "document", "source_documents": docs},
            config={},
        )

        await course_manager.create_course(
            tenant_id="t1",
            source_type="document",
            source_documents=docs,
        )

        created_course = mock_repository.create_course.call_args[0][0]
        assert created_course.source_config["source_type"] == "document"
        assert created_course.source_config["source_documents"] == docs
        assert created_course.source_config["source_url"] is None

    async def test_creates_course_with_config(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        cfg = {"max_lessons": 50, "video_preset": "fast"}
        mock_repository.create_course.return_value = Course(
            course_id="c3",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web"},
            config=cfg,
        )

        await course_manager.create_course(
            tenant_id="t1",
            source_type="web",
            config=cfg,
        )

        created_course = mock_repository.create_course.call_args[0][0]
        assert created_course.config == cfg

    async def test_create_course_repo_exception(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Repository raises during create_course."""
        mock_repository.create_course.side_effect = RuntimeError("DB connection lost")

        with pytest.raises(RuntimeError, match="DB connection lost"):
            await course_manager.create_course(
                tenant_id="t1",
                source_type="web",
            )


class TestHandleCallback:
    async def test_updates_lesson_on_callback(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="c1",
            position=1,
            title="Test",
            pipeline_job_id="pipeline-001",
            pipeline_status="queued",
        )
        mock_repository.get_lessons.return_value = [lesson]

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="pipeline-001",
            status="completed",
            video_url="https://cdn.example.com/v.mp4",
        )

        # Verify update_lesson was called with the correct lesson_id as first arg
        mock_repository.update_lesson.assert_called_once()
        call_args = mock_repository.update_lesson.call_args
        assert call_args[0][0] == "l1"  # lesson_id is positional arg
        assert call_args[1]["pipeline_status"] == "completed"
        assert call_args[1]["video_url"] == "https://cdn.example.com/v.mp4"

    async def test_callback_with_error(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="c1",
            position=1,
            title="Test",
            pipeline_job_id="pipeline-001",
            pipeline_status="processing",
        )
        mock_repository.get_lessons.return_value = [lesson]

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="pipeline-001",
            status="failed",
            error="Rendering timeout",
        )

        call_kwargs = mock_repository.update_lesson.call_args[1]
        assert call_kwargs["pipeline_status"] == "failed"
        assert call_kwargs["error"] == "Rendering timeout"
        # video_url should NOT be in the update when not provided
        assert "video_url" not in call_kwargs

    async def test_callback_with_artifacts(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="c1",
            position=1,
            title="Test",
            pipeline_job_id="pipeline-001",
            pipeline_status="processing",
        )
        mock_repository.get_lessons.return_value = [lesson]
        artifacts = {"audio_url": "/audio.mp3", "thumbnail_url": "/thumb.png"}

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="pipeline-001",
            status="completed",
            video_url="https://cdn.example.com/v.mp4",
            artifacts=artifacts,
        )

        call_kwargs = mock_repository.update_lesson.call_args[1]
        assert call_kwargs["artifacts"] == artifacts

    async def test_callback_triggers_progress_update(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """After updating a lesson, handle_callback should update progress."""
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="c1",
            position=1,
            title="Test",
            pipeline_job_id="pipeline-001",
            pipeline_status="queued",
        )
        mock_repository.get_lessons.return_value = [lesson]

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="pipeline-001",
            status="completed",
        )

        # update_lesson should be called before progress tracking
        mock_repository.update_lesson.assert_called_once()

    async def test_ignores_unknown_job(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_lessons.return_value = []
        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="unknown-job",
            status="completed",
        )
        mock_repository.update_lesson.assert_not_called()

    async def test_ignores_unknown_job_among_multiple_lessons(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """When there are lessons but none match the job_id."""
        lessons = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="Lesson 1",
                pipeline_job_id="job-AAA",
                pipeline_status="queued",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="Lesson 2",
                pipeline_job_id="job-BBB",
                pipeline_status="queued",
            ),
        ]
        mock_repository.get_lessons.return_value = lessons

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="job-NONEXISTENT",
            status="completed",
        )
        mock_repository.update_lesson.assert_not_called()

    async def test_callback_matches_correct_lesson_among_multiple(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Ensure the correct lesson is updated when multiple exist."""
        lessons = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="Lesson 1",
                pipeline_job_id="job-AAA",
                pipeline_status="queued",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="Lesson 2",
                pipeline_job_id="job-BBB",
                pipeline_status="queued",
            ),
        ]
        mock_repository.get_lessons.return_value = lessons

        await course_manager.handle_callback(
            course_id="c1",
            tenant_id="t1",
            job_id="job-BBB",
            status="completed",
            video_url="https://cdn.example.com/l2.mp4",
        )

        # Should update l2, not l1
        call_args = mock_repository.update_lesson.call_args
        assert call_args[0][0] == "l2"
        assert call_args[1]["video_url"] == "https://cdn.example.com/l2.mp4"


class TestRetryLesson:
    async def test_raises_for_non_retryable(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="course-001",
            position=1,
            title="Test",
            pipeline_status="completed",
        )
        mock_repository.get_lesson.return_value = lesson

        with pytest.raises(ValueError, match="not retryable"):
            await course_manager.retry_lesson("course-001", "l1", tenant_id="t1")

    async def test_raises_for_missing_course(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_course.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await course_manager.retry_lesson("nonexistent", "l1", tenant_id="t1")

    async def test_raises_for_missing_lesson(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_lesson.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await course_manager.retry_lesson("course-001", "nonexistent", tenant_id="t1")

    async def test_retry_failed_lesson(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="course-001",
            position=1,
            title="Failed",
            pipeline_status="failed",
        )
        mock_repository.get_lesson.return_value = lesson
        result = await course_manager.retry_lesson("course-001", "l1", tenant_id="t1")
        assert isinstance(result, str)

    async def test_retry_skipped_lesson_allowed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        """Skipped lessons should be retryable too."""
        mock_repository.get_course.return_value = sample_course
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="course-001",
            position=1,
            title="Skipped",
            pipeline_status="skipped",
        )
        mock_repository.get_lesson.return_value = lesson
        result = await course_manager.retry_lesson("course-001", "l1", tenant_id="t1")
        assert isinstance(result, str)

    async def test_retry_processing_lesson_raises(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        """A lesson with status 'processing' should not be retryable."""
        mock_repository.get_course.return_value = sample_course
        lesson = Lesson(
            lesson_id="l1",
            module_id="m1",
            course_id="course-001",
            position=1,
            title="In Progress",
            pipeline_status="processing",
        )
        mock_repository.get_lesson.return_value = lesson
        with pytest.raises(ValueError, match="not retryable"):
            await course_manager.retry_lesson("course-001", "l1", tenant_id="t1")


class TestRunPipeline:
    async def test_full_pipeline_success(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        mock_planner: AsyncMock,
    ) -> None:
        course = Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={
                "generate_module_intros": False,
                "generate_course_intro": False,
                "generate_slide_decks": False,
            },
        )
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = []
        mock_orchestrator_client.create_pipeline.return_value = {"job_id": "job-1"}
        mock_orchestrator_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "results": {"analyze": {"pages": [], "raw": {}}},
        }

        await course_manager.run_pipeline("c1", tenant_id="t1")

        # Verify status transitions happened in the correct ORDER
        update_calls = [c.kwargs for c in mock_repository.update_course.call_args_list]
        statuses = [c["status"] for c in update_calls if "status" in c]
        assert statuses == [
            "analyzing",
            "planning_curriculum",
            "curriculum_ready",
            "producing",
            "supplementary",
            "assembling",
            "completed",
        ]

    async def test_pipeline_failure_sets_failed_status(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        course = Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={},
        )
        mock_repository.get_course.return_value = course
        mock_orchestrator_client.create_pipeline.side_effect = RuntimeError("Analysis failed")

        with pytest.raises(RuntimeError, match="Analysis failed"):
            await course_manager.run_pipeline("c1", tenant_id="t1")

        update_calls = [c.kwargs for c in mock_repository.update_course.call_args_list]
        statuses = [c["status"] for c in update_calls if "status" in c]
        assert "failed" in statuses
        # Verify error message was passed in the failed update
        failed_calls = [c for c in update_calls if c.get("status") == "failed"]
        assert len(failed_calls) == 1
        assert failed_calls[0]["error"] == "Analysis failed"

    async def test_pipeline_not_found_raises(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_course.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await course_manager.run_pipeline("nonexistent", tenant_id="t1")

    async def test_pipeline_logs_events(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        mock_planner: AsyncMock,
    ) -> None:
        """Verify build log events are written at each pipeline phase."""
        course = Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={
                "generate_module_intros": False,
                "generate_course_intro": False,
                "generate_slide_decks": False,
            },
        )
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = []
        mock_orchestrator_client.create_pipeline.return_value = {"job_id": "job-1"}
        mock_orchestrator_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "results": {"analyze": {"pages": [], "raw": {}}},
        }

        await course_manager.run_pipeline("c1", tenant_id="t1")

        log_events = [c[0][0].event_type for c in mock_repository.add_build_log.call_args_list]
        assert "analysis_started" in log_events
        assert "analysis_completed" in log_events
        assert "curriculum_planning_started" in log_events
        assert "curriculum_ready" in log_events
        assert "production_started" in log_events
        assert "course_completed" in log_events


class TestRunAnalysis:
    async def test_web_source(
        self,
        course_manager: CourseManager,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        mock_orchestrator_client.create_pipeline.return_value = {"job_id": "job-1"}
        mock_orchestrator_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "results": {"analyze": {"pages": []}},
        }

        result = await course_manager._run_analysis(
            {"source_type": "web", "source_url": "https://example.com"}
        )
        assert result == {"pages": []}

        # Verify the orchestrator was called with correct parameters
        mock_orchestrator_client.create_pipeline.assert_called_once_with(
            mode="full",
            url="https://example.com",
            config_overrides={"stages_enabled": ["crawl", "analyze"]},
        )
        mock_orchestrator_client.wait_for_pipeline.assert_called_once_with("job-1", timeout=600.0)

    async def test_document_source(
        self,
        course_manager: CourseManager,
    ) -> None:
        docs = [{"title": "Doc 1", "content": "text"}]
        result = await course_manager._run_analysis(
            {"source_type": "document", "source_documents": docs}
        )
        assert result["pages"] == docs
        assert result["raw"]["source_type"] == "document"

    async def test_unsupported_source_raises(
        self,
        course_manager: CourseManager,
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported source type"):
            await course_manager._run_analysis({"source_type": "ftp"})

    async def test_web_source_without_url_raises(
        self,
        course_manager: CourseManager,
    ) -> None:
        """Web source with no URL should raise 'Unsupported source type' because
        the code falls through to the else branch when source_url is falsy."""
        with pytest.raises(ValueError, match="Unsupported source type"):
            await course_manager._run_analysis({"source_type": "web"})


class TestPersistCurriculum:
    async def test_creates_modules_and_lessons(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_curriculum: dict[str, Any],
    ) -> None:
        await course_manager._persist_curriculum("course-001", sample_curriculum)

        mock_repository.create_modules_bulk.assert_called_once()
        modules = mock_repository.create_modules_bulk.call_args[0][0]
        assert len(modules) == 2
        assert modules[0].title == "Introduction"
        assert modules[0].position == 1
        assert modules[0].course_id == "course-001"
        assert modules[1].title == "API Deep Dive"
        assert modules[1].position == 2
        assert modules[1].course_id == "course-001"

        mock_repository.create_lessons_bulk.assert_called_once()
        lessons = mock_repository.create_lessons_bulk.call_args[0][0]
        assert len(lessons) == 3
        # Verify lesson details
        assert lessons[0].title == "Platform Overview"
        assert lessons[0].topic == "What is Example Platform"
        assert lessons[0].position == 1
        assert lessons[0].course_id == "course-001"
        assert lessons[1].title == "Installation Guide"
        assert lessons[1].position == 2
        assert lessons[2].title == "Authentication"
        assert lessons[2].position == 1  # first lesson in second module

    async def test_empty_curriculum(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        modules, lessons = await course_manager._persist_curriculum("c1", {"modules": []})
        assert modules == []
        assert lessons == []

    async def test_lessons_get_correct_module_ids(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_curriculum: dict[str, Any],
    ) -> None:
        """Each lesson's module_id should match its parent module."""
        await course_manager._persist_curriculum("course-001", sample_curriculum)

        modules = mock_repository.create_modules_bulk.call_args[0][0]
        lessons = mock_repository.create_lessons_bulk.call_args[0][0]

        mod1_id = modules[0].module_id
        mod2_id = modules[1].module_id

        # First two lessons belong to module 1
        assert lessons[0].module_id == mod1_id
        assert lessons[1].module_id == mod1_id
        # Third lesson belongs to module 2
        assert lessons[2].module_id == mod2_id

    async def test_default_values_for_missing_fields(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Test that missing lesson fields get sensible defaults."""
        curriculum = {
            "modules": [
                {
                    "lessons": [
                        {},  # No title, topic, description, etc.
                    ],
                },
            ],
        }
        await course_manager._persist_curriculum("c1", curriculum)

        modules = mock_repository.create_modules_bulk.call_args[0][0]
        assert modules[0].title == "Module 1"  # default title

        lessons = mock_repository.create_lessons_bulk.call_args[0][0]
        assert lessons[0].title == "Lesson 1"  # default title
        assert lessons[0].video_type == "mixed"
        assert lessons[0].target_duration_seconds == 120


class TestAccessors:
    async def test_list_courses(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.list_courses.return_value = ([], 0)
        result = await course_manager.list_courses("t1", offset=5, limit=10)
        assert result == ([], 0)
        # Verify EXACT parameters passed to repository
        mock_repository.list_courses.assert_called_once_with("t1", offset=5, limit=10)

    async def test_list_courses_default_params(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.list_courses.return_value = ([], 0)
        await course_manager.list_courses("tenant-abc")
        # Verify default offset=0 and limit=20 are passed
        mock_repository.list_courses.assert_called_once_with("tenant-abc", offset=0, limit=20)

    async def test_count_courses(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.count_courses.return_value = 42
        result = await course_manager.count_courses("t1")
        assert result == 42
        # Verify exact tenant_id passed
        mock_repository.count_courses.assert_called_once_with("t1")

    async def test_get_modules(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        expected = [
            Module(module_id="m1", course_id="c1", position=1, title="Mod 1"),
        ]
        mock_repository.get_modules.return_value = expected
        result = await course_manager.get_modules("c1", tenant_id="t1")
        assert result == expected
        mock_repository.get_modules.assert_called_once_with("c1", tenant_id="t1")

    async def test_get_lessons(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        expected = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="Lesson A",
            ),
        ]
        mock_repository.get_lessons.return_value = expected
        result = await course_manager.get_lessons("c1", tenant_id="t1")
        assert result == expected
        mock_repository.get_lessons.assert_called_once_with("c1", tenant_id="t1")

    async def test_get_module(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        expected = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_module.return_value = expected
        result = await course_manager.get_module("m1")
        assert result is expected
        mock_repository.get_module.assert_called_once_with("m1")

    async def test_get_module_not_found(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_module.return_value = None
        result = await course_manager.get_module("nonexistent")
        assert result is None
        mock_repository.get_module.assert_called_once_with("nonexistent")

    async def test_get_lessons_for_module(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        expected = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="Test",
            ),
        ]
        mock_repository.get_lessons_for_module.return_value = expected
        result = await course_manager.get_lessons_for_module("m1")
        assert result == expected
        mock_repository.get_lessons_for_module.assert_called_once_with("m1")

    async def test_get_lesson(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        expected = Lesson(lesson_id="l1", module_id="m1", course_id="c1", position=1, title="Test")
        mock_repository.get_lesson.return_value = expected
        result = await course_manager.get_lesson("l1")
        assert result is expected
        mock_repository.get_lesson.assert_called_once_with("l1")

    async def test_get_lesson_not_found(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_lesson.return_value = None
        result = await course_manager.get_lesson("nonexistent")
        assert result is None

    async def test_delete_course(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.delete_course.return_value = True
        result = await course_manager.delete_course("c1", tenant_id="t1")
        assert result is True
        mock_repository.delete_course.assert_called_once_with("c1", tenant_id="t1")

    async def test_delete_course_not_found(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Deleting a nonexistent course returns False."""
        mock_repository.delete_course.return_value = False
        result = await course_manager.delete_course("nonexistent", tenant_id="t1")
        assert result is False
        mock_repository.delete_course.assert_called_once_with("nonexistent", tenant_id="t1")

    async def test_build_manifest(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = []
        result = await course_manager.build_manifest("course-001", tenant_id="t1")
        assert result["course_id"] == "course-001"
        assert result["total_modules"] == 0
        assert result["total_lessons"] == 0
        assert result["modules"] == []

    async def test_get_course(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        result = await course_manager.get_course("course-001", tenant_id="t1")
        assert result is sample_course
        mock_repository.get_course.assert_called_once_with("course-001", tenant_id="t1")

    async def test_get_course_not_found(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_course.return_value = None
        result = await course_manager.get_course("nonexistent", tenant_id="t1")
        assert result is None
        mock_repository.get_course.assert_called_once_with("nonexistent", tenant_id="t1")


class TestUpdateModuleStatuses:
    """Tests for _update_module_statuses — module status derived from lesson statuses."""

    async def test_update_module_statuses_all_completed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """All lessons completed -> module status = 'completed'."""
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="L2",
                pipeline_status="completed",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_called_once_with("m1", status="completed")

    async def test_update_module_statuses_mixed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Some completed + some failed -> module status = 'partial'."""
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="L2",
                pipeline_status="failed",
            ),
            Lesson(
                lesson_id="l3",
                module_id="m1",
                course_id="c1",
                position=3,
                title="L3",
                pipeline_status="completed",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_called_once_with("m1", status="partial")

    async def test_update_module_statuses_all_failed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """All failed/skipped -> module status = 'failed'."""
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="failed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="L2",
                pipeline_status="skipped",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_called_once_with("m1", status="failed")

    async def test_update_module_statuses_still_in_progress(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Some lessons still pending -> module not updated."""
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="L2",
                pipeline_status="pending",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_not_called()

    async def test_update_module_statuses_empty_module(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Module with no lessons -> skipped (no update_module call)."""
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = []

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_not_called()

    async def test_update_module_statuses_multiple_modules(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """Multiple modules get independent status computations."""
        mod1 = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mod2 = Module(module_id="m2", course_id="c1", position=2, title="Mod 2")
        mock_repository.get_modules.return_value = [mod1, mod2]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m2",
                course_id="c1",
                position=1,
                title="L2",
                pipeline_status="failed",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        assert mock_repository.update_module.call_count == 2
        calls = {
            c.args[0]: c.kwargs["status"] for c in mock_repository.update_module.call_args_list
        }
        assert calls["m1"] == "completed"
        assert calls["m2"] == "failed"


class TestPipelineCompletesWhenSupplementaryFails:
    """Supplementary content failures must not prevent pipeline completion."""

    async def test_pipeline_completes_when_supplementary_fails(
        self,
        mock_repository: AsyncMock,
        mock_analysis_client: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        mock_planner: AsyncMock,
        mock_slide_client: AsyncMock,
    ) -> None:
        """All supplementary tasks raise, but course still reaches 'completed'."""
        manager = CourseManager(
            repository=mock_repository,
            analysis_client=mock_analysis_client,
            orchestrator_client=mock_orchestrator_client,
            planner=mock_planner,
            slide_client=mock_slide_client,
            concurrency_limit=2,
            max_retries=1,
            lesson_timeout=60.0,
        )

        course = Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={
                "generate_module_intros": True,
                "generate_course_intro": True,
                "generate_slide_decks": True,
            },
        )
        mock_repository.get_course.return_value = course

        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = []

        mock_orchestrator_client.create_pipeline.return_value = {"job_id": "job-1"}
        mock_orchestrator_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "results": {"analyze": {"pages": [], "raw": {}}},
        }
        # Make supplementary calls fail — module intros and course intro use
        # create_pipeline -> wait_for_pipeline, slide client uses its own methods.
        # We need the FIRST create_pipeline call (analysis) to succeed,
        # then subsequent ones (supplementary) to fail.
        call_count = {"n": 0}
        original_return = {"job_id": "job-1"}

        async def create_pipeline_side_effect(**kwargs: Any) -> dict[str, Any]:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Analysis phase
                return original_return
            # Supplementary phase — fail
            raise RuntimeError("supplementary API down")

        mock_orchestrator_client.create_pipeline.side_effect = create_pipeline_side_effect
        mock_slide_client.create_slide_deck.side_effect = RuntimeError("slide service down")

        await manager.run_pipeline("c1", tenant_id="t1")

        # Verify pipeline reached 'completed' despite supplementary failures
        update_calls = [c.kwargs for c in mock_repository.update_course.call_args_list]
        statuses = [c["status"] for c in update_calls if "status" in c]
        assert "completed" in statuses
        # 'failed' should NOT be the final status
        assert statuses[-1] == "completed"


class TestCourseDeliveryVerdict:
    """The course's own status must tell the truth about what was delivered.

    Before the fix, ``run_pipeline`` wrote ``status="completed"`` with no
    branch on the dispatcher's counts. Because a lesson that exhausts its
    retries is marked "skipped" and raises nothing, a course where every
    lesson was lost reported ``status="completed"``, ``error=None`` and a
    manifest whose ``video_url`` fields were all null.
    """

    @staticmethod
    def _course() -> Course:
        return Course(
            course_id="c1",
            tenant_id="t1",
            status="planning",
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={
                "generate_module_intros": False,
                "generate_course_intro": False,
                "generate_slide_decks": False,
            },
        )

    @staticmethod
    def _lessons(statuses: list[str]) -> list[Lesson]:
        return [
            Lesson(
                lesson_id=f"l{i}",
                module_id="m1",
                course_id="c1",
                position=i,
                title=f"L{i}",
                pipeline_status=st,
                video_url=("https://cdn.example.com/v.mp4" if st == "completed" else None),
            )
            for i, st in enumerate(statuses, start=1)
        ]

    async def _run(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        statuses: list[str],
    ) -> list[dict[str, Any]]:
        mock_repository.get_course.return_value = self._course()
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = self._lessons(statuses)
        mock_orchestrator_client.create_pipeline.return_value = {"job_id": "job-1"}
        mock_orchestrator_client.wait_for_pipeline.return_value = {
            "status": "completed",
            "video_url": "https://cdn.example.com/v.mp4",
            "results": {"analyze": {"pages": [], "raw": {}}},
        }
        await course_manager.run_pipeline("c1", tenant_id="t1")
        return [c.kwargs for c in mock_repository.update_course.call_args_list]

    async def test_all_lessons_lost_is_not_completed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """Ten lessons, none delivered -> the course is 'failed', not 'completed'."""
        calls = await self._run(
            course_manager, mock_repository, mock_orchestrator_client, ["skipped"] * 10
        )
        final = [c for c in calls if c.get("status") in ("completed", "partial", "failed")][-1]
        assert final["status"] == "failed"
        assert final["status"] != "completed"
        assert final["error"] == "0 of 10 lessons produced a video"

    async def test_some_lessons_lost_is_partial_not_completed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """A course that lost some lessons is distinguishable from one that lost none."""
        calls = await self._run(
            course_manager,
            mock_repository,
            mock_orchestrator_client,
            ["completed"] * 8 + ["skipped", "failed"],
        )
        final = [c for c in calls if c.get("status") in ("completed", "partial", "failed")][-1]
        assert final["status"] == "partial"
        assert final["error"] == "2 of 10 lessons did not produce a video"

    async def test_one_lost_lesson_is_still_partial(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """Any loss is visible. There is no 'close enough' threshold."""
        calls = await self._run(
            course_manager,
            mock_repository,
            mock_orchestrator_client,
            ["completed"] * 9 + ["skipped"],
        )
        final = [c for c in calls if c.get("status") in ("completed", "partial", "failed")][-1]
        assert final["status"] == "partial"

    async def test_fully_successful_course_still_reports_completed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """THE regression guard: a genuinely complete course must report completed."""
        calls = await self._run(
            course_manager, mock_repository, mock_orchestrator_client, ["completed"] * 10
        )
        statuses = [c["status"] for c in calls if "status" in c]
        assert statuses == [
            "analyzing",
            "planning_curriculum",
            "curriculum_ready",
            "producing",
            "supplementary",
            "assembling",
            "completed",
        ]
        final = [c for c in calls if c.get("status") == "completed"][-1]
        assert final["error"] is None
        progress = [c for c in calls if "progress_pct" in c][-1]
        assert progress["progress_pct"] == 100
        assert progress["completed_lessons"] == 10

    async def test_degraded_lessons_do_not_count_as_delivered(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """A course of only 'partial' lessons has delivered nothing clean."""
        calls = await self._run(
            course_manager, mock_repository, mock_orchestrator_client, ["partial"] * 4
        )
        final = [c for c in calls if c.get("status") in ("completed", "partial", "failed")][-1]
        assert final["status"] == "failed"

    async def test_progress_is_not_100_for_a_course_that_delivered_nothing(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """Progress used to read 100% because failures counted as progress."""
        calls = await self._run(
            course_manager, mock_repository, mock_orchestrator_client, ["skipped"] * 10
        )
        progress = [c for c in calls if "progress_pct" in c][-1]
        assert progress["progress_pct"] == 0
        assert progress["completed_lessons"] == 0
        assert progress["skipped_lessons"] == 10


class TestModuleIntroDoesNotRescueTheModule:
    async def test_module_with_all_lessons_failed_stays_failed(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
    ) -> None:
        """The intro rendering must not overwrite the lesson-derived verdict.

        ``_generate_module_intro`` runs AFTER ``_update_module_statuses`` and
        used to write ``status="completed"`` unconditionally, so a module whose
        every lesson failed reported "completed" because its 60-second intro
        rendered.
        """
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        lessons = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="skipped",
            ),
        ]
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = lessons

        await course_manager._update_module_statuses("c1", tenant_id="t1")
        assert mock_repository.update_module.call_args_list[-1].kwargs == {"status": "failed"}

        await course_manager._generate_module_intro(mod, lessons, {}, "c1")

        module_statuses = [
            c.kwargs["status"]
            for c in mock_repository.update_module.call_args_list
            if "status" in c.kwargs
        ]
        assert module_statuses == ["failed"], (
            "the intro must not write a module status at all — it does not know "
            "anything about whether the lessons were delivered"
        )

    async def test_degraded_lesson_does_not_leave_module_pending(
        self,
        course_manager: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        """'partial' is terminal; it used to fall outside the terminal set.

        A module holding one then stayed 'pending' forever underneath a course
        that called itself completed.
        """
        mod = Module(module_id="m1", course_id="c1", position=1, title="Mod 1")
        mock_repository.get_modules.return_value = [mod]
        mock_repository.get_lessons.return_value = [
            Lesson(
                lesson_id="l1",
                module_id="m1",
                course_id="c1",
                position=1,
                title="L1",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="m1",
                course_id="c1",
                position=2,
                title="L2",
                pipeline_status="partial",
            ),
        ]

        await course_manager._update_module_statuses("c1", tenant_id="t1")

        mock_repository.update_module.assert_called_once_with("m1", status="partial")
