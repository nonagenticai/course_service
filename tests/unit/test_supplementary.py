"""Tests for supplementary content generation (module intros, course intro, slide decks)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, call

import pytest

from src.database.models import Course, Lesson, Module
from src.services.course_manager import CourseManager
from src.services.curriculum.planner import CurriculumPlanner


@pytest.fixture
def mock_planner() -> AsyncMock:
    return AsyncMock(spec=CurriculumPlanner)


@pytest.fixture
def manager_with_slides(
    mock_repository: AsyncMock,
    mock_analysis_client: AsyncMock,
    mock_orchestrator_client: AsyncMock,
    mock_planner: AsyncMock,
    mock_slide_client: AsyncMock,
) -> CourseManager:
    return CourseManager(
        repository=mock_repository,
        analysis_client=mock_analysis_client,
        orchestrator_client=mock_orchestrator_client,
        planner=mock_planner,
        slide_client=mock_slide_client,
        concurrency_limit=2,
        max_retries=1,
        lesson_timeout=60.0,
    )


@pytest.fixture
def manager_no_slides(
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
        slide_client=None,
        concurrency_limit=2,
        max_retries=1,
        lesson_timeout=60.0,
    )


class TestGenerateSupplementaryContent:
    async def test_dispatches_module_intros(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="Test Course",
            source_config={},
            config={},
        )
        mock_repository.update_module = AsyncMock()

        config = {
            "generate_module_intros": True,
            "generate_course_intro": False,
            "generate_slide_decks": False,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        # One pipeline call per module
        assert mock_orchestrator_client.create_pipeline.call_count == 2

        # Collect all create_pipeline user_context values
        pipeline_calls = mock_orchestrator_client.create_pipeline.call_args_list
        intro_contexts = [c.kwargs["user_context"] for c in pipeline_calls]

        # Find the call for each module by module_title
        mod1_ctx = next(c for c in intro_contexts if c["module_title"] == "Introduction")
        mod2_ctx = next(c for c in intro_contexts if c["module_title"] == "API Deep Dive")

        # Module 1: "Introduction" has lessons "Platform Overview" and "Installation Guide"
        assert mod1_ctx["is_module_intro"] is True
        assert mod1_ctx["lesson_titles"] == ["Platform Overview", "Installation Guide"]
        assert mod1_ctx["target_duration"] == 60
        assert "Introduction to Introduction" in mod1_ctx["lesson_topic"]
        assert mod1_ctx["lesson_title"] == "Introduction — Module Introduction"

        # Module 2: "API Deep Dive" has lesson "Authentication"
        assert mod2_ctx["is_module_intro"] is True
        assert mod2_ctx["lesson_titles"] == ["Authentication"]
        assert mod2_ctx["module_title"] == "API Deep Dive"

        # Each call should pass analysis as prior_results
        for c in pipeline_calls:
            assert c.kwargs["prior_results"] == {"analyze": sample_analysis}
            assert c.kwargs["config_overrides"] == {"target_duration_minutes": 1.0}

        # Modules should be updated with intro_video_job_id and intro_video_url
        update_calls = mock_repository.update_module.call_args_list
        job_id_updates = [c for c in update_calls if "intro_video_job_id" in c.kwargs]
        video_url_updates = [c for c in update_calls if "intro_video_url" in c.kwargs]
        assert len(job_id_updates) == 2
        assert len(video_url_updates) == 2

    async def test_skips_intros_when_disabled(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons

        config = {
            "generate_module_intros": False,
            "generate_course_intro": False,
            "generate_slide_decks": False,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        mock_orchestrator_client.create_pipeline.assert_not_called()

    async def test_dispatches_course_intro(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="My Course",
            source_config={},
            config={},
        )

        config = {
            "generate_module_intros": False,
            "generate_course_intro": True,
            "generate_slide_decks": False,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        mock_orchestrator_client.create_pipeline.assert_called_once()
        call_kwargs = mock_orchestrator_client.create_pipeline.call_args[1]
        ctx = call_kwargs["user_context"]
        assert ctx["is_course_intro"] is True
        assert "My Course" in ctx["lesson_title"]
        assert ctx["course_title"] == "My Course"
        assert ctx["target_duration"] == 90
        # module_titles should include both modules sorted by position
        assert ctx["module_titles"] == ["Introduction", "API Deep Dive"]

    async def test_dispatches_slide_decks(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_slide_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons
        mock_repository.update_module = AsyncMock()

        config = {
            "generate_module_intros": False,
            "generate_course_intro": False,
            "generate_slide_decks": True,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        assert mock_slide_client.create_slide_deck.call_count == 2
        assert mock_slide_client.wait_for_slide_deck.call_count == 2

        # Verify each create_slide_deck call has correct module_title and lessons
        slide_calls = mock_slide_client.create_slide_deck.call_args_list
        slide_args_by_title = {c.kwargs["module_title"]: c.kwargs for c in slide_calls}

        # Module "Introduction" should have 2 lessons
        intro_args = slide_args_by_title["Introduction"]
        assert len(intro_args["lessons"]) == 2
        assert intro_args["lessons"][0]["title"] == "Platform Overview"
        assert intro_args["lessons"][1]["title"] == "Installation Guide"

        # Module "API Deep Dive" should have 1 lesson
        api_args = slide_args_by_title["API Deep Dive"]
        assert len(api_args["lessons"]) == 1
        assert api_args["lessons"][0]["title"] == "Authentication"

        # Each module should be updated with slide_deck_path
        update_calls = mock_repository.update_module.call_args_list
        slide_path_updates = [c for c in update_calls if "slide_deck_path" in c.kwargs]
        assert len(slide_path_updates) == 2
        # Verify the specific paths
        for c in slide_path_updates:
            assert c.kwargs["slide_deck_path"] == "/slides/module-intro.pptx"

    async def test_skips_slides_without_client(
        self,
        manager_no_slides: CourseManager,
        mock_repository: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons

        config = {
            "generate_module_intros": False,
            "generate_course_intro": False,
            "generate_slide_decks": True,
        }
        await manager_no_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )
        # No crash, no calls — silently skipped


class TestModuleIntroGeneration:
    async def test_intro_sets_job_id_and_video_url(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_analysis: dict,
    ) -> None:
        module = Module(
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Intro Module",
        )
        lessons = [
            Lesson(
                lesson_id="l1", module_id="mod-001", course_id="course-001", position=1, title="L1"
            ),
            Lesson(
                lesson_id="l2", module_id="mod-001", course_id="course-001", position=2, title="L2"
            ),
        ]
        mock_repository.update_module = AsyncMock()

        await manager_with_slides._generate_module_intro(
            module, lessons, sample_analysis, "course-001"
        )

        # Verify pipeline was created with correct intro context
        call_kwargs = mock_orchestrator_client.create_pipeline.call_args[1]
        ctx = call_kwargs["user_context"]
        assert ctx["is_module_intro"] is True
        assert ctx["module_title"] == "Intro Module"
        assert ctx["lesson_titles"] == ["L1", "L2"]
        assert ctx["target_duration"] == 60
        assert ctx["lesson_topic"] == "Introduction to Intro Module"
        assert ctx["lesson_title"] == "Intro Module — Module Introduction"
        assert ctx["module_description"] == ""
        assert call_kwargs["prior_results"] == {"analyze": sample_analysis}
        assert call_kwargs["config_overrides"] == {"target_duration_minutes": 1.0}

        # Verify module was updated: first with job_id, then with video_url
        update_calls = mock_repository.update_module.call_args_list
        assert len(update_calls) == 2

        # First call: set intro_video_job_id on the correct module
        assert update_calls[0] == call("mod-001", intro_video_job_id="pipeline-001")

        # Second call: intro_video_url ONLY. This assertion used to require
        # status="completed", pinning the bug — the intro ran after
        # _update_module_statuses and overwrote the lesson-derived verdict, so
        # a module whose every lesson failed reported "completed" because its
        # 60-second intro rendered.
        assert update_calls[1] == call(
            "mod-001",
            intro_video_url="https://cdn.example.com/videos/output.mp4",
        )
        assert "status" not in update_calls[1].kwargs

    async def test_intro_logs_failure_without_raising(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_analysis: dict,
    ) -> None:
        module = Module(
            module_id="mod-001", course_id="course-001", position=1, title="Fail Module"
        )
        mock_orchestrator_client.create_pipeline.side_effect = RuntimeError("Pipeline error")
        mock_repository.update_module = AsyncMock()

        # Should not raise
        await manager_with_slides._generate_module_intro(module, [], sample_analysis, "course-001")

        # Should log the failure event with error details
        log_calls = mock_repository.add_build_log.call_args_list
        events = [call.args[0].event_type for call in log_calls]
        assert "module_intro_failed" in events

        # Verify the failure log includes the error message
        failed_log = next(
            c.args[0] for c in log_calls if c.args[0].event_type == "module_intro_failed"
        )
        assert failed_log.details == {"error": "Pipeline error"}
        assert failed_log.module_id == "mod-001"

    async def test_intro_duration_is_60_seconds(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_analysis: dict,
    ) -> None:
        """Module intro duration is set to 60 seconds / 1.0 minute."""
        module = Module(
            module_id="mod-001", course_id="course-001", position=1, title="Test Module"
        )
        mock_repository.update_module = AsyncMock()

        await manager_with_slides._generate_module_intro(module, [], sample_analysis, "course-001")

        call_kwargs = mock_orchestrator_client.create_pipeline.call_args.kwargs
        assert call_kwargs["user_context"]["target_duration"] == 60
        assert call_kwargs["config_overrides"]["target_duration_minutes"] == 1.0

    async def test_intro_partial_failure_others_still_run(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        """If one module intro fails, others still run to completion."""
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons
        mock_repository.update_module = AsyncMock()

        call_count = 0

        async def _fail_first_only(**kwargs: Any) -> dict[str, str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first module failed")
            return {"job_id": "job-second"}

        mock_orchestrator_client.create_pipeline.side_effect = _fail_first_only

        config = {
            "generate_module_intros": True,
            "generate_course_intro": False,
            "generate_slide_decks": False,
        }
        # Should not raise — gather with return_exceptions=True handles partial failure
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        # Both modules attempted (2 create_pipeline calls)
        assert call_count == 2

        # The successful module should have been updated with job_id
        job_id_updates = [
            c
            for c in mock_repository.update_module.call_args_list
            if "intro_video_job_id" in c.kwargs
        ]
        assert len(job_id_updates) >= 1

        # The failed module should have a failure log
        log_calls = mock_repository.add_build_log.call_args_list
        events = [c.args[0].event_type for c in log_calls]
        assert "module_intro_failed" in events


class TestCourseIntroGeneration:
    async def test_course_intro_dispatches_pipeline(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="Awesome Course",
            description="A great course",
            source_config={},
            config={},
        )

        await manager_with_slides._generate_course_intro(
            "course-001", sample_modules, sample_analysis, tenant_id="t1"
        )

        call_kwargs = mock_orchestrator_client.create_pipeline.call_args[1]
        ctx = call_kwargs["user_context"]
        assert ctx["is_course_intro"] is True
        assert ctx["course_title"] == "Awesome Course"
        assert ctx["course_description"] == "A great course"
        assert ctx["target_duration"] == 90
        assert ctx["module_titles"] == ["Introduction", "API Deep Dive"]
        assert ctx["lesson_topic"] == "Welcome to Awesome Course"
        assert ctx["lesson_title"] == "Awesome Course — Introduction"
        assert call_kwargs["config_overrides"]["target_duration_minutes"] == 1.5
        assert call_kwargs["prior_results"] == {"analyze": sample_analysis}

    async def test_course_intro_stores_url_in_config(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="Test",
            source_config={},
            config={"existing_key": "value"},
        )

        await manager_with_slides._generate_course_intro(
            "course-001", sample_modules, sample_analysis, tenant_id="t1"
        )

        update_calls = mock_repository.update_course.call_args_list
        config_update = next(
            (c for c in update_calls if "config" in c.kwargs),
            None,
        )
        assert config_update is not None
        updated_config = config_update.kwargs["config"]
        assert updated_config["intro_video_url"] == "https://cdn.example.com/videos/output.mp4"
        assert updated_config["existing_key"] == "value"

    async def test_course_intro_returns_when_course_missing(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_course.return_value = None

        await manager_with_slides._generate_course_intro(
            "nonexistent", sample_modules, sample_analysis, tenant_id="t1"
        )

        mock_orchestrator_client.create_pipeline.assert_not_called()

    async def test_course_intro_handles_failure(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        sample_modules: list[Module],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="Test",
            source_config={},
            config={},
        )
        mock_orchestrator_client.create_pipeline.side_effect = RuntimeError("fail")

        await manager_with_slides._generate_course_intro(
            "course-001", sample_modules, sample_analysis, tenant_id="t1"
        )

        log_calls = mock_repository.add_build_log.call_args_list
        events = [call.args[0].event_type for call in log_calls]
        assert "course_intro_failed" in events

        # Verify the error details are recorded
        failed_log = next(
            c.args[0] for c in log_calls if c.args[0].event_type == "course_intro_failed"
        )
        assert failed_log.details == {"error": "fail"}


class TestSlideDeckGeneration:
    async def test_generates_slide_deck(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_slide_client: AsyncMock,
    ) -> None:
        module = Module(module_id="mod-001", course_id="course-001", position=1, title="Module 1")
        lessons = [
            Lesson(
                lesson_id="l1",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="L1",
                topic="Topic 1",
                description="Desc 1",
            ),
        ]
        mock_repository.update_module = AsyncMock()

        await manager_with_slides._generate_slide_deck(module, lessons, "course-001")

        mock_slide_client.create_slide_deck.assert_called_once_with(
            module_title="Module 1",
            lessons=[{"title": "L1", "topic": "Topic 1", "description": "Desc 1"}],
        )
        mock_slide_client.wait_for_slide_deck.assert_called_once_with(
            "slide-job-001", timeout=300.0
        )
        mock_repository.update_module.assert_called_once_with(
            "mod-001", slide_deck_path="/slides/module-intro.pptx"
        )

    async def test_slide_deck_handles_failure(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_slide_client: AsyncMock,
    ) -> None:
        module = Module(module_id="mod-001", course_id="course-001", position=1, title="Module 1")
        mock_slide_client.create_slide_deck.side_effect = RuntimeError("slide error")
        mock_repository.update_module = AsyncMock()

        await manager_with_slides._generate_slide_deck(module, [], "course-001")

        log_calls = mock_repository.add_build_log.call_args_list
        events = [call.args[0].event_type for call in log_calls]
        assert "slide_deck_failed" in events

        # Verify the error details
        failed_log = next(
            c.args[0] for c in log_calls if c.args[0].event_type == "slide_deck_failed"
        )
        assert failed_log.details == {"error": "slide error"}
        assert failed_log.module_id == "mod-001"

    async def test_slide_deck_skips_without_client(
        self,
        manager_no_slides: CourseManager,
        mock_repository: AsyncMock,
    ) -> None:
        module = Module(module_id="mod-001", course_id="course-001", position=1, title="Module 1")
        mock_repository.update_module = AsyncMock()

        await manager_no_slides._generate_slide_deck(module, [], "course-001")

        mock_repository.update_module.assert_not_called()

    async def test_slide_deck_lesson_data_sorted_by_position(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_slide_client: AsyncMock,
    ) -> None:
        """Lessons should be sorted by position before building slide data."""
        module = Module(module_id="mod-001", course_id="course-001", position=1, title="Module 1")
        # Pass lessons out of order
        lessons = [
            Lesson(
                lesson_id="l3",
                module_id="mod-001",
                course_id="course-001",
                position=3,
                title="Third",
                topic="T3",
                description="D3",
            ),
            Lesson(
                lesson_id="l1",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="First",
                topic="T1",
                description="D1",
            ),
            Lesson(
                lesson_id="l2",
                module_id="mod-001",
                course_id="course-001",
                position=2,
                title="Second",
                topic="T2",
                description="D2",
            ),
        ]
        mock_repository.update_module = AsyncMock()

        await manager_with_slides._generate_slide_deck(module, lessons, "course-001")

        slide_lessons = mock_slide_client.create_slide_deck.call_args.kwargs["lessons"]
        assert slide_lessons[0]["title"] == "First"
        assert slide_lessons[1]["title"] == "Second"
        assert slide_lessons[2]["title"] == "Third"


class TestAllSupplementaryTogether:
    async def test_all_three_enabled(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        mock_slide_client: AsyncMock,
        sample_modules: list[Module],
        sample_lessons: list[Lesson],
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = sample_modules
        mock_repository.get_lessons.return_value = sample_lessons
        mock_repository.get_course.return_value = Course(
            course_id="course-001",
            tenant_id="t1",
            status="supplementary",
            title="Full Course",
            source_config={},
            config={},
        )
        mock_repository.update_module = AsyncMock()

        config = {
            "generate_module_intros": True,
            "generate_course_intro": True,
            "generate_slide_decks": True,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        # 2 module intros + 1 course intro = 3 orchestrator calls
        assert mock_orchestrator_client.create_pipeline.call_count == 3
        # 2 slide decks
        assert mock_slide_client.create_slide_deck.call_count == 2

        # Verify the orchestrator calls include the right contexts
        pipeline_contexts = [
            c.kwargs["user_context"]
            for c in mock_orchestrator_client.create_pipeline.call_args_list
        ]
        module_intro_contexts = [c for c in pipeline_contexts if c.get("is_module_intro")]
        course_intro_contexts = [c for c in pipeline_contexts if c.get("is_course_intro")]
        assert len(module_intro_contexts) == 2
        assert len(course_intro_contexts) == 1
        assert course_intro_contexts[0]["course_title"] == "Full Course"

    async def test_empty_modules_no_crash(
        self,
        manager_with_slides: CourseManager,
        mock_repository: AsyncMock,
        mock_orchestrator_client: AsyncMock,
        mock_slide_client: AsyncMock,
        sample_analysis: dict,
    ) -> None:
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = []

        config = {
            "generate_module_intros": True,
            "generate_course_intro": True,
            "generate_slide_decks": True,
        }
        await manager_with_slides._generate_supplementary_content(
            "course-001", config, sample_analysis, tenant_id="t1"
        )

        # No crash with empty modules; course_intro requires modules so it's skipped too
        mock_orchestrator_client.create_pipeline.assert_not_called()
        mock_slide_client.create_slide_deck.assert_not_called()
