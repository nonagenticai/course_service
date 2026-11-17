"""Unit tests for course_service API schemas."""

import json

import pytest
from pydantic import ValidationError

from src.api.schemas import (
    BuildLogResponse,
    CallbackPayload,
    CourseConfig,
    CourseDetailResponse,
    CourseListResponse,
    CourseProgressResponse,
    CourseResponse,
    CourseStatus,
    CreateCourseRequest,
    LessonPipelineStatus,
    LessonResponse,
    ModuleResponse,
    ModuleStatus,
    VideoType,
)


class TestCourseConfig:
    def test_defaults(self):
        config = CourseConfig()
        # llm_model is an optional override; None means "use service-configured model".
        assert config.llm_model is None
        assert config.max_lessons == 200
        assert config.target_lesson_duration_seconds == 120
        assert config.concurrency_limit == 10
        assert config.max_retries == 3
        assert config.video_preset == "broadcast"
        assert config.generate_module_intros is True
        assert config.generate_course_intro is True
        assert config.generate_slide_decks is True

    def test_custom_values(self):
        config = CourseConfig(max_lessons=50, concurrency_limit=5)
        assert config.max_lessons == 50
        assert config.concurrency_limit == 5

    def test_max_lessons_zero_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CourseConfig(max_lessons=0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("max_lessons",) for e in errors)

    def test_max_lessons_above_500_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CourseConfig(max_lessons=501)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("max_lessons",) for e in errors)

    def test_max_lessons_boundary_values(self):
        """ge=1, le=500 — boundaries should be accepted."""
        low = CourseConfig(max_lessons=1)
        assert low.max_lessons == 1
        high = CourseConfig(max_lessons=500)
        assert high.max_lessons == 500

    def test_concurrency_limit_zero_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CourseConfig(concurrency_limit=0)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("concurrency_limit",) for e in errors)

    def test_concurrency_limit_above_20_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CourseConfig(concurrency_limit=21)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("concurrency_limit",) for e in errors)

    def test_concurrency_limit_boundary_values(self):
        low = CourseConfig(concurrency_limit=1)
        assert low.concurrency_limit == 1
        high = CourseConfig(concurrency_limit=20)
        assert high.concurrency_limit == 20

    def test_target_duration_below_60_rejected(self):
        with pytest.raises(ValidationError):
            CourseConfig(target_lesson_duration_seconds=59)

    def test_target_duration_above_180_rejected(self):
        with pytest.raises(ValidationError):
            CourseConfig(target_lesson_duration_seconds=181)

    def test_max_retries_negative_rejected(self):
        with pytest.raises(ValidationError):
            CourseConfig(max_retries=-1)

    def test_max_retries_above_10_rejected(self):
        with pytest.raises(ValidationError):
            CourseConfig(max_retries=11)

    def test_json_round_trip(self):
        """Serialize to JSON and back, ensuring no data loss."""
        original = CourseConfig(max_lessons=100, concurrency_limit=5, video_preset="fast")
        json_str = original.model_dump_json()
        restored = CourseConfig.model_validate_json(json_str)
        assert restored.max_lessons == 100
        assert restored.concurrency_limit == 5
        assert restored.video_preset == "fast"
        # All other fields should retain defaults
        assert restored.llm_model is None
        assert restored.generate_module_intros is True


class TestCreateCourseRequest:
    def test_minimal(self):
        req = CreateCourseRequest(source_type="web", source_url="https://example.com")
        assert req.source_type == "web"
        assert req.source_url == "https://example.com"
        assert req.config.llm_model is None
        assert req.source_documents is None
        assert req.user_context is None

    def test_document_source(self):
        req = CreateCourseRequest(
            source_type="document",
            source_documents=[{"path": "/tmp/doc.pdf", "format": "pdf"}],
        )
        assert req.source_type == "document"
        assert req.source_documents is not None and len(req.source_documents) == 1
        assert req.source_documents[0]["path"] == "/tmp/doc.pdf"

    def test_with_user_context(self):
        ctx = {"target_audience": "beginners", "tone": "friendly"}
        req = CreateCourseRequest(source_type="web", user_context=ctx)
        assert req.user_context == ctx

    def test_with_custom_config(self):
        req = CreateCourseRequest(
            source_type="web",
            config=CourseConfig(max_lessons=50, concurrency_limit=3),
        )
        assert req.config.max_lessons == 50
        assert req.config.concurrency_limit == 3

    def test_source_url_max_length(self):
        """source_url has max_length=2048."""
        long_url = "https://example.com/" + "a" * 2030
        assert len(long_url) > 2048
        with pytest.raises(ValidationError) as exc_info:
            CreateCourseRequest(source_type="web", source_url=long_url)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_url",) for e in errors)

    def test_source_url_at_max_length_accepted(self):
        url = "https://example.com/" + "a" * (2048 - len("https://example.com/"))
        assert len(url) == 2048
        req = CreateCourseRequest(source_type="web", source_url=url)
        assert req.source_url is not None
        assert len(req.source_url) == 2048

    def test_missing_source_type_rejected(self):
        with pytest.raises(ValidationError) as exc_info:
            CreateCourseRequest.model_validate({})
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_type",) for e in errors)

    @pytest.mark.parametrize("invalid", ["", "unknown", "WEB", "web; drop", "pdf"])
    def test_source_type_rejects_invalid_values(self, invalid: str) -> None:
        with pytest.raises(ValidationError) as exc_info:
            CreateCourseRequest(source_type=invalid)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_type",) for e in errors)

    @pytest.mark.parametrize("valid", ["web", "document", "app"])
    def test_source_type_accepts_allowed_values(self, valid: str) -> None:
        req = CreateCourseRequest(source_type=valid)
        assert req.source_type == valid

    def test_source_documents_max_items(self) -> None:
        """source_documents list is capped to prevent DoS via huge payloads."""
        docs = [{"id": i} for i in range(201)]
        with pytest.raises(ValidationError) as exc_info:
            CreateCourseRequest(source_type="document", source_documents=docs)
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_documents",) for e in errors)

    def test_json_round_trip(self):
        original = CreateCourseRequest(
            source_type="web",
            source_url="https://example.com",
            source_documents=None,
            config=CourseConfig(max_lessons=75),
            user_context={"tone": "professional"},
        )
        json_str = original.model_dump_json()
        restored = CreateCourseRequest.model_validate_json(json_str)
        assert restored.source_type == "web"
        assert restored.source_url == "https://example.com"
        assert restored.config.max_lessons == 75
        assert restored.user_context == {"tone": "professional"}


class TestStatusEnums:
    def test_course_status_values(self):
        """Verify all CourseStatus members have expected string values."""
        expected = {
            "PLANNING": "planning",
            "ANALYZING": "analyzing",
            "CURRICULUM_READY": "curriculum_ready",
            "PRODUCING": "producing",
            "COMPLETED": "completed",
            "PARTIAL": "partial",
            "FAILED": "failed",
            "CANCELLED": "cancelled",
        }
        for member_name, string_val in expected.items():
            assert CourseStatus[member_name] == string_val
        # Verify we haven't missed any members
        assert len(CourseStatus) == len(expected)

    def test_module_status_values(self):
        expected = {
            "PENDING": "pending",
            "PRODUCING": "producing",
            "COMPLETED": "completed",
            "PARTIAL": "partial",
            "FAILED": "failed",
        }
        for member_name, string_val in expected.items():
            assert ModuleStatus[member_name] == string_val
        assert len(ModuleStatus) == len(expected)

    def test_lesson_pipeline_status_values(self):
        expected = {
            "PENDING": "pending",
            "QUEUED": "queued",
            "PROCESSING": "processing",
            "COMPLETED": "completed",
            "PARTIAL": "partial",
            "FAILED": "failed",
            "SKIPPED": "skipped",
        }
        for member_name, string_val in expected.items():
            assert LessonPipelineStatus[member_name] == string_val
        assert len(LessonPipelineStatus) == len(expected)

    def test_video_type_values(self):
        expected = {
            "BROWSER_RECORDING": "browser_recording",
            "SLIDES": "slides",
            "AI_VIDEO": "ai_video",
            "TITLE_CARD": "title_card",
            "MIXED": "mixed",
        }
        for member_name, string_val in expected.items():
            assert VideoType[member_name] == string_val
        assert len(VideoType) == len(expected)

    def test_enums_usable_in_pydantic_models(self):
        """Enum values should be accepted by Pydantic models that use them."""
        resp = CourseResponse(id="c1", tenant_id="t1", status=CourseStatus.PRODUCING)
        assert resp.status == "producing"

        lesson = LessonResponse(
            lesson_id="l1",
            module_id="m1",
            position=1,
            title="Test",
            video_type=VideoType.SLIDES,
            pipeline_status=LessonPipelineStatus.QUEUED,
        )
        assert lesson.video_type == "slides"
        assert lesson.pipeline_status == "queued"


class TestCallbackPayload:
    def test_minimal(self):
        payload = CallbackPayload(job_id="j1", status="completed")
        assert payload.job_id == "j1"
        assert payload.status == "completed"
        assert payload.error is None
        assert payload.video_url is None
        assert payload.stages_completed is None
        assert payload.artifacts is None

    def test_full_payload(self):
        payload = CallbackPayload(
            job_id="j1",
            status="completed",
            error=None,
            video_url="https://cdn.example.com/v.mp4",
            stages_completed=["crawl", "analyze", "narrative", "render"],
            artifacts={"audio_url": "/audio.mp3", "thumbnail": "/thumb.png"},
        )
        assert payload.video_url == "https://cdn.example.com/v.mp4"
        assert payload.stages_completed == ["crawl", "analyze", "narrative", "render"]
        assert payload.artifacts is not None
        assert payload.artifacts["audio_url"] == "/audio.mp3"

    def test_error_payload(self):
        payload = CallbackPayload(job_id="j1", status="failed", error="Rendering timeout")
        assert payload.status == "failed"
        assert payload.error == "Rendering timeout"

    def test_json_serialization(self):
        payload = CallbackPayload(
            job_id="j1",
            status="completed",
            video_url="/path/to/video.mp4",
            stages_completed=["crawl", "analyze"],
            artifacts={"key": "val"},
        )
        json_str = payload.model_dump_json()
        data = json.loads(json_str)
        assert data["job_id"] == "j1"
        assert data["status"] == "completed"
        assert data["video_url"] == "/path/to/video.mp4"
        assert data["stages_completed"] == ["crawl", "analyze"]
        assert data["artifacts"] == {"key": "val"}
        assert data["error"] is None

    def test_json_round_trip(self):
        original = CallbackPayload(
            job_id="j1",
            status="completed",
            video_url="/v.mp4",
            artifacts={"nested": {"deep": True}},
        )
        restored = CallbackPayload.model_validate_json(original.model_dump_json())
        assert restored.job_id == original.job_id
        assert restored.status == original.status
        assert restored.video_url == original.video_url
        assert restored.artifacts == original.artifacts

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError) as exc_info:
            CallbackPayload.model_validate({})
        errors = exc_info.value.errors()
        loc_names = {e["loc"][0] for e in errors}
        assert "job_id" in loc_names
        assert "status" in loc_names


class TestResponses:
    def test_course_response_defaults(self):
        resp = CourseResponse(id="c1", tenant_id="t1")
        assert resp.status == "planning"
        assert resp.progress_pct == 0
        assert resp.total_lessons == 0
        assert resp.completed_lessons == 0
        assert resp.failed_lessons == 0
        assert resp.skipped_lessons == 0
        assert resp.title is None
        assert resp.description is None
        assert resp.bundle_url is None
        assert resp.error is None

    def test_course_response_full(self):
        resp = CourseResponse(
            id="c1",
            tenant_id="t1",
            status=CourseStatus.COMPLETED,
            title="My Course",
            description="A great course",
            progress_pct=100,
            total_lessons=10,
            completed_lessons=9,
            failed_lessons=1,
            skipped_lessons=0,
            bundle_url="https://storage.example.com/bundle.zip",
        )
        assert resp.progress_pct == 100
        assert resp.completed_lessons == 9
        assert resp.bundle_url == "https://storage.example.com/bundle.zip"

    def test_lesson_response_defaults(self):
        resp = LessonResponse(lesson_id="l1", module_id="m1", position=0, title="Test")
        assert resp.pipeline_status == "pending"
        assert resp.video_type == "mixed"
        assert resp.target_duration_seconds == 120
        assert resp.retry_count == 0
        assert resp.video_url is None
        assert resp.error is None

    def test_lesson_response_json_round_trip(self):
        original = LessonResponse(
            lesson_id="l1",
            module_id="m1",
            position=3,
            title="Advanced Topics",
            topic="Webhooks",
            video_type=VideoType.AI_VIDEO,
            pipeline_status=LessonPipelineStatus.COMPLETED,
            video_url="https://cdn.example.com/v.mp4",
        )
        restored = LessonResponse.model_validate_json(original.model_dump_json())
        assert restored.lesson_id == "l1"
        assert restored.position == 3
        assert restored.video_type == "ai_video"
        assert restored.pipeline_status == "completed"
        assert restored.video_url == "https://cdn.example.com/v.mp4"

    def test_module_response_defaults(self):
        resp = ModuleResponse(module_id="m1", course_id="c1", position=1, title="Module 1")
        assert resp.status == "pending"
        assert resp.lessons == []
        assert resp.intro_video_url is None
        assert resp.slide_deck_path is None

    def test_module_response_with_lessons(self):
        lesson = LessonResponse(lesson_id="l1", module_id="m1", position=1, title="Lesson 1")
        resp = ModuleResponse(
            module_id="m1",
            course_id="c1",
            position=1,
            title="Module 1",
            lessons=[lesson],
        )
        assert len(resp.lessons) == 1
        assert resp.lessons[0].lesson_id == "l1"

    def test_course_detail_response(self):
        resp = CourseDetailResponse(
            id="c1",
            tenant_id="t1",
            curriculum={"modules": []},
            manifest={"course_id": "c1", "modules": []},
        )
        assert resp.modules == []
        assert resp.curriculum is not None
        assert resp.manifest is not None
        # Inherits from CourseResponse
        assert resp.status == "planning"

    def test_course_list_response(self):
        resp = CourseListResponse(
            courses=[
                CourseResponse(id="c1", tenant_id="t1"),
                CourseResponse(id="c2", tenant_id="t1"),
            ],
            total=2,
            limit=20,
            offset=0,
        )
        assert len(resp.courses) == 2
        assert resp.total == 2

    def test_course_list_response_defaults(self):
        resp = CourseListResponse()
        assert resp.courses == []
        assert resp.total == 0
        assert resp.limit == 20
        assert resp.offset == 0

    def test_progress_response(self):
        resp = CourseProgressResponse(
            id="c1",
            status="producing",
            progress_pct=50,
            total_lessons=10,
            completed_lessons=5,
            failed_lessons=1,
            skipped_lessons=0,
            pending_lessons=4,
        )
        assert resp.pending_lessons == 4
        assert resp.progress_pct == 50
        assert (
            resp.total_lessons
            == resp.completed_lessons
            + resp.failed_lessons
            + resp.skipped_lessons
            + resp.pending_lessons
        )

    def test_progress_response_missing_required(self):
        with pytest.raises(ValidationError):
            CourseProgressResponse.model_validate({"id": "c1"})

    def test_build_log_response(self):
        resp = BuildLogResponse(
            log_id="log-1",
            id="c1",
            event_type="course_created",
            details={"key": "value"},
        )
        assert resp.log_id == "log-1"
        assert resp.event_type == "course_created"
        assert resp.details == {"key": "value"}
        assert resp.lesson_id is None
        assert resp.module_id is None

    def test_build_log_response_json_round_trip(self):
        original = BuildLogResponse(
            log_id="log-1",
            id="c1",
            event_type="lesson_completed",
            lesson_id="l1",
            module_id="m1",
            details={"duration_ms": 1500},
        )
        restored = BuildLogResponse.model_validate_json(original.model_dump_json())
        assert restored.log_id == "log-1"
        assert restored.lesson_id == "l1"
        assert restored.module_id == "m1"
        assert restored.details == {"duration_ms": 1500}
