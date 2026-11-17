"""Shared test fixtures for course_service."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import clear_settings_cache
from src.database.models import Base, Course, Lesson, Module
from src.database.repository import CourseRepository

# Set test environment variables before importing any app code
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres",
)
# w2v_common >= 0.15.0 dropped the localhost defaults on RedisSettings.url and
# StorageSettings.minio_endpoint; seed module-level so Settings() instantiation
# during pytest collection doesn't fail. The autouse _clear_settings fixture
# re-applies them after its env-clearing sweep.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("STORAGE_MINIO_ENDPOINT", "localhost:9000")

# Capture the test DB URL BEFORE the autouse _clear_settings fixture
# monkeypatches it for app-side env. CI sets DATABASE_URL to its
# course_service_ci postgres; locally falls back to the default above.
_TEST_DATABASE_URL = os.environ["DATABASE_URL"]

# ---------------------------------------------------------------------------
# Settings cache management
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings(monkeypatch):
    """Remove CI env vars that interfere with config-default tests."""
    for key in list(os.environ):
        if any(
            key.startswith(p)
            for p in (
                "DATABASE_",
                "AUTH_",
                "REDIS_",
                "API_",
                "STORAGE_",
                "LOG_",
                "CACHE_",
                "TRACING_",
                "LLM_",
                "FFMPEG_",
                "COURSE_",
                "DOWNSTREAM_",
            )
        ):
            monkeypatch.delenv(key, raising=False)
    # Restore w2v_common 0.15.0 fail-fast fields with placeholders.
    monkeypatch.setenv("DATABASE_URL", _TEST_DATABASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("STORAGE_MINIO_ENDPOINT", "localhost:9000")
    # COURSE_LLM_MODEL has no default in code — production sources from configmap.
    monkeypatch.setenv("COURSE_LLM_MODEL", "test-model")
    clear_settings_cache()
    yield
    clear_settings_cache()


@pytest.fixture
def sample_analysis() -> dict[str, Any]:
    """Sample analysis data from analysis_service."""
    return {
        "pages": [
            {
                "title": "Getting Started",
                "url": "https://example.com/getting-started",
                "page_type": "content",
                "summary": "Introduction to the platform",
                "headings": ["Overview", "Installation", "First Steps"],
            },
            {
                "title": "API Reference",
                "url": "https://example.com/api",
                "page_type": "documentation",
                "summary": "Complete API documentation",
                "headings": ["Authentication", "Endpoints", "Rate Limits"],
            },
            {
                "title": "Advanced Features",
                "url": "https://example.com/advanced",
                "page_type": "content",
                "summary": "Advanced usage patterns",
                "headings": ["Webhooks", "Batch Processing", "Custom Integrations"],
            },
        ],
        "raw": {
            "site_analysis": {
                "site_name": "Example Platform",
                "description": "A comprehensive platform for developers",
            },
            "narrative_extraction": {
                "key_themes": ["developer tools", "API integration", "automation"],
            },
        },
        "site_analysis": {
            "site_name": "Example Platform",
            "description": "A comprehensive platform for developers",
        },
    }


@pytest.fixture
def sample_curriculum() -> dict[str, Any]:
    """Sample curriculum from LLM generation."""
    return {
        "title": "Example Platform Mastery",
        "description": "Complete course on the Example Platform",
        "modules": [
            {
                "title": "Introduction",
                "description": "Getting started with the platform",
                "lessons": [
                    {
                        "title": "Platform Overview",
                        "topic": "What is Example Platform",
                        "description": "Understanding the basics",
                        "video_type": "mixed",
                        "target_duration_seconds": 120,
                    },
                    {
                        "title": "Installation Guide",
                        "topic": "Setting up the environment",
                        "description": "Step-by-step installation",
                        "video_type": "mixed",
                        "target_duration_seconds": 120,
                    },
                ],
            },
            {
                "title": "API Deep Dive",
                "description": "Understanding the API",
                "lessons": [
                    {
                        "title": "Authentication",
                        "topic": "API authentication mechanisms",
                        "description": "How to authenticate",
                        "video_type": "mixed",
                        "target_duration_seconds": 120,
                    },
                ],
            },
        ],
    }


@pytest.fixture
def sample_course() -> Course:
    """Sample course model instance."""
    return Course(
        course_id="course-001",
        tenant_id="test-tenant",
        status="planning",
        source_config={"source_type": "web", "source_url": "https://example.com"},
        config={"max_lessons": 200, "target_lesson_duration_seconds": 120},
    )


@pytest.fixture
def sample_module() -> Module:
    """Sample module model instance."""
    return Module(
        module_id="mod-001",
        course_id="course-001",
        position=1,
        title="Introduction",
        description="Getting started",
    )


@pytest.fixture
def sample_lessons() -> list[Lesson]:
    """Sample lesson model instances."""
    return [
        Lesson(
            lesson_id="lesson-001",
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Platform Overview",
            topic="What is Example Platform",
            target_duration_seconds=120,
            pipeline_status="pending",
        ),
        Lesson(
            lesson_id="lesson-002",
            module_id="mod-001",
            course_id="course-001",
            position=2,
            title="Installation Guide",
            topic="Setting up the environment",
            target_duration_seconds=120,
            pipeline_status="pending",
        ),
        Lesson(
            lesson_id="lesson-003",
            module_id="mod-002",
            course_id="course-001",
            position=1,
            title="Authentication",
            topic="API authentication",
            target_duration_seconds=120,
            pipeline_status="completed",
            video_url="https://cdn.example.com/videos/lesson-003.mp4",
        ),
    ]


# ---------------------------------------------------------------------------
# Real database fixtures (postgres+psycopg, TRUNCATE-based isolation)
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    """Per-test async postgres engine with TRUNCATE-based isolation."""
    eng = create_async_engine(_TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if Base.metadata.sorted_tables:
            tables = ", ".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
            await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
def repo(session_factory):
    return CourseRepository(session_factory)


@pytest.fixture
def mock_repository() -> AsyncMock:
    """Mock CourseRepository."""
    repo = AsyncMock()
    repo.create_course = AsyncMock()
    repo.get_course = AsyncMock()
    repo.list_courses = AsyncMock(return_value=[])
    repo.update_course = AsyncMock()
    repo.delete_course = AsyncMock(return_value=True)
    repo.create_modules_bulk = AsyncMock()
    repo.create_lessons_bulk = AsyncMock()
    repo.get_modules = AsyncMock(return_value=[])
    repo.get_module = AsyncMock()
    repo.get_lessons = AsyncMock(return_value=[])
    repo.get_lessons_for_module = AsyncMock(return_value=[])
    repo.get_lesson = AsyncMock()
    repo.update_lesson = AsyncMock()
    repo.add_build_log = AsyncMock()
    repo.list_incomplete_courses = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_orchestrator_client() -> AsyncMock:
    """Mock OrchestratorClient."""
    client = AsyncMock()
    client.create_pipeline = AsyncMock(return_value={"job_id": "pipeline-001"})
    client.wait_for_pipeline = AsyncMock(
        return_value={
            "status": "completed",
            "video_url": "https://cdn.example.com/videos/output.mp4",
            "artifacts": {},
        }
    )
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_analysis_client() -> AsyncMock:
    """Mock AnalysisClient."""
    client = AsyncMock()
    client.submit_analysis = AsyncMock(return_value="analysis-job-001")
    client.wait_for_analysis = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_slide_client() -> AsyncMock:
    """Mock SlideClient."""
    client = AsyncMock()
    client.create_slide_deck = AsyncMock(return_value="slide-job-001")
    client.wait_for_slide_deck = AsyncMock(
        return_value={
            "status": "completed",
            "pptx_path": "/slides/module-intro.pptx",
        }
    )
    client.close = AsyncMock()
    return client


@pytest.fixture
def sample_modules() -> list[Module]:
    """Sample module model instances for testing."""
    return [
        Module(
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Introduction",
            description="Getting started",
        ),
        Module(
            module_id="mod-002",
            course_id="course-001",
            position=2,
            title="API Deep Dive",
            description="Understanding the API",
        ),
    ]


@pytest.fixture
def mock_update_module() -> AsyncMock:
    """Mock for repository.update_module."""
    return AsyncMock()
