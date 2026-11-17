"""Request/response Pydantic models for course_service."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class CourseStatus(StrEnum):
    PLANNING = "planning"
    ANALYZING = "analyzing"
    CURRICULUM_READY = "curriculum_ready"
    PRODUCING = "producing"
    COMPLETED = "completed"
    # Terminal, and the course is usable, but at least one lesson did not
    # produce a video. Distinct from COMPLETED so a caller cannot mistake a
    # lossy course for a clean one; distinct from FAILED so it does not throw
    # away work that was actually delivered.
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModuleStatus(StrEnum):
    PENDING = "pending"
    PRODUCING = "producing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    # _update_module_statuses has always written "failed"; the enum never
    # listed it.
    FAILED = "failed"


class LessonPipelineStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    # Terminal but not delivered: the orchestrator reported partial /
    # completed_with_errors, or reported completed with no video_url.
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class VideoType(StrEnum):
    BROWSER_RECORDING = "browser_recording"
    SLIDES = "slides"
    AI_VIDEO = "ai_video"
    TITLE_CARD = "title_card"
    MIXED = "mixed"


# --- Request models ---


class CourseConfig(BaseModel):
    # Optional override; when None the service uses settings.course.llm_model (configmap COURSE_LLM_MODEL).
    llm_model: str | None = Field(default=None)
    max_lessons: int = Field(default=200, ge=1, le=500)
    target_lesson_duration_seconds: int = Field(default=120, ge=60, le=180)
    concurrency_limit: int = Field(default=10, ge=1, le=20)
    max_retries: int = Field(default=3, ge=0, le=10)
    video_preset: str = Field(default="broadcast")
    generate_module_intros: bool = True
    generate_course_intro: bool = True
    generate_slide_decks: bool = True


class CreateCourseRequest(BaseModel):
    source_type: str = Field(
        pattern="^(web|document|app)$",
        description="Input source type: web, document, app",
    )
    source_url: str | None = Field(default=None, max_length=2048)
    source_documents: list[dict[str, Any]] | None = Field(default=None, max_length=200)
    config: CourseConfig = Field(default_factory=CourseConfig)
    user_context: dict[str, Any] | None = Field(
        default=None,
        description="User-provided context: target_audience, tone, description, etc.",
    )


# --- Response models ---


class LessonResponse(BaseModel):
    lesson_id: str
    module_id: str
    position: int
    title: str
    topic: str | None = None
    description: str | None = None
    video_type: VideoType = VideoType.MIXED
    target_duration_seconds: int = 120
    pipeline_status: LessonPipelineStatus = LessonPipelineStatus.PENDING
    retry_count: int = 0
    video_url: str | None = None
    error: str | None = None


class ModuleResponse(BaseModel):
    module_id: str
    course_id: str
    position: int
    title: str
    description: str | None = None
    status: ModuleStatus = ModuleStatus.PENDING
    intro_video_url: str | None = None
    slide_deck_path: str | None = None
    lessons: list[LessonResponse] = []


class CourseResponse(BaseModel):
    id: str
    tenant_id: str
    status: CourseStatus = CourseStatus.PLANNING
    title: str | None = None
    description: str | None = None
    progress_pct: int = 0
    total_lessons: int = 0
    completed_lessons: int = 0
    failed_lessons: int = 0
    skipped_lessons: int = 0
    bundle_url: str | None = None
    error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class CourseDetailResponse(CourseResponse):
    modules: list[ModuleResponse] = []
    curriculum: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None


class CourseListResponse(BaseModel):
    courses: list[CourseResponse] = []
    total: int = 0
    limit: int = 20
    offset: int = 0


class CourseProgressResponse(BaseModel):
    id: str
    status: str
    progress_pct: int
    total_lessons: int
    completed_lessons: int
    failed_lessons: int
    skipped_lessons: int
    pending_lessons: int


class BuildLogResponse(BaseModel):
    log_id: str
    id: str
    event_type: str
    lesson_id: str | None = None
    module_id: str | None = None
    details: dict[str, Any] | None = None
    created_at: str | None = None


class CallbackPayload(BaseModel):
    """Payload from orchestrator webhook callback."""

    job_id: str
    status: str
    error: str | None = None
    video_url: str | None = None
    stages_completed: list[str] | None = None
    artifacts: dict[str, Any] | None = None
