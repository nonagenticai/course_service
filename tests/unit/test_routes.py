"""Tests for src.api.routes — REST API endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.database.models import Course, Lesson, Module


@pytest.fixture
def mock_course_manager() -> AsyncMock:
    mgr = AsyncMock()
    mgr.create_course = AsyncMock()
    mgr.get_course = AsyncMock()
    mgr.list_courses = AsyncMock(return_value=[])
    mgr.count_courses = AsyncMock(return_value=0)
    mgr.get_modules = AsyncMock(return_value=[])
    mgr.get_lessons = AsyncMock(return_value=[])
    mgr.get_module = AsyncMock()
    mgr.get_lessons_for_module = AsyncMock(return_value=[])
    mgr.get_lesson = AsyncMock()
    mgr.delete_course = AsyncMock(return_value=True)
    mgr.build_manifest = AsyncMock()
    mgr.handle_callback = AsyncMock()
    mgr.run_pipeline = AsyncMock()
    mgr.retry_lesson = AsyncMock()
    return mgr


@pytest.fixture
async def client(mock_course_manager: AsyncMock) -> AsyncIterator[AsyncClient]:
    """Build an AsyncClient against the app with mocked course_manager.

    We import the app here and patch its state to avoid triggering the
    real lifespan (database, Redis, etc.). The three production exception
    handlers are mounted via the FastAPI constructor so the error
    envelope from ``src.asgi`` applies in unit tests too.
    """
    from fastapi import FastAPI
    from w2v_common.asgi import register_error_envelope_handlers

    from src.api.routes import router as courses_router

    test_app = FastAPI()
    # Same canonical envelope handlers the service registers in src/asgi.py.
    register_error_envelope_handlers(test_app)
    test_app.include_router(courses_router)
    test_app.state.course_manager = mock_course_manager

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_course(**overrides: Any) -> Course:
    defaults: dict[str, Any] = {
        "course_id": "course-001",
        "tenant_id": "default",
        "status": "completed",
        "title": "Test Course",
        "description": "A test course",
        "source_config": {"source_type": "web"},
        "config": {},
        "progress_pct": 100,
        "total_lessons": 3,
        "completed_lessons": 3,
        "failed_lessons": 0,
        "skipped_lessons": 0,
        "bundle_url": None,
        "error": None,
    }
    defaults.update(overrides)
    return Course(**defaults)


class TestCreateCourse:
    async def test_create_course_returns_course_id(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.create_course.return_value = MagicMock(
            course_id="course-new", status="planning"
        )

        resp = await client.post(
            "/api/courses",
            json={"source_type": "web", "source_url": "https://example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # Verify all three expected keys are present with correct values
        assert data == {
            "id": "course-new",
            "status": "planning",
            "message": "Course creation started",
        }

    async def test_create_course_passes_parameters_to_manager(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """Verify that create_course passes all body fields to the manager."""
        mock_course_manager.create_course.return_value = MagicMock(
            course_id="c-99", status="planning"
        )

        resp = await client.post(
            "/api/courses",
            json={
                "source_type": "document",
                "source_url": "https://docs.example.com",
                "config": {"max_lessons": 50, "target_lesson_duration_seconds": 90},
                "user_context": {"tone": "casual"},
            },
        )
        assert resp.status_code == 200

        call_kwargs = mock_course_manager.create_course.call_args.kwargs
        assert call_kwargs["tenant_id"] == "default"
        assert call_kwargs["source_type"] == "document"
        assert call_kwargs["source_url"] == "https://docs.example.com"
        assert call_kwargs["config"]["max_lessons"] == 50
        assert call_kwargs["config"]["target_lesson_duration_seconds"] == 90

    async def test_create_course_fires_pipeline_in_background(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.create_course.return_value = MagicMock(
            course_id="c-bg", status="planning"
        )

        resp = await client.post(
            "/api/courses",
            json={"source_type": "web", "source_url": "https://example.com"},
        )
        assert resp.status_code == 200
        # run_pipeline is dispatched via asyncio.create_task, so it is called
        # but may not be awaited by the time the response returns.
        mock_course_manager.run_pipeline.assert_called_once()
        call_kwargs = mock_course_manager.run_pipeline.call_args
        assert call_kwargs.args[0] == "c-bg"
        assert call_kwargs.kwargs["user_context"] is None

    async def test_create_course_missing_source_type_returns_422(
        self,
        client: AsyncClient,
    ) -> None:
        resp = await client.post("/api/courses", json={})
        assert resp.status_code == 422


class TestGetCourse:
    async def test_get_course_returns_404_when_missing(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.get_course.return_value = None

        resp = await client.get("/api/courses/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["error"]["message"] == "Course not found"

    async def test_get_course_returns_full_response(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        course = _make_course()
        mod = Module(
            module_id="mod-1",
            course_id="course-001",
            position=1,
            title="Mod 1",
            status="completed",
        )
        lesson = Lesson(
            lesson_id="les-1",
            module_id="mod-1",
            course_id="course-001",
            position=1,
            title="Lesson 1",
            pipeline_status="completed",
        )
        mock_course_manager.get_course.return_value = course
        mock_course_manager.get_modules.return_value = [mod]
        mock_course_manager.get_lessons.return_value = [lesson]

        resp = await client.get("/api/courses/course-001")
        assert resp.status_code == 200
        data = resp.json()

        # Verify all top-level fields
        assert data["id"] == "course-001"
        assert data["tenant_id"] == "default"
        assert data["status"] == "completed"
        assert data["title"] == "Test Course"
        assert data["description"] == "A test course"
        assert data["progress_pct"] == 100
        assert data["total_lessons"] == 3
        assert data["completed_lessons"] == 3
        assert data["failed_lessons"] == 0
        assert data["skipped_lessons"] == 0
        assert data["bundle_url"] is None
        assert data["error"] is None
        assert data["config"] == {}

        # Verify modules array
        assert len(data["modules"]) == 1
        assert data["modules"][0] == {
            "module_id": "mod-1",
            "title": "Mod 1",
            "status": "completed",
            "position": 1,
        }

        # Verify lessons array
        assert len(data["lessons"]) == 1
        assert data["lessons"][0] == {
            "lesson_id": "les-1",
            "title": "Lesson 1",
            "pipeline_status": "completed",
        }


class TestGetProgress:
    async def test_get_progress_returns_all_fields(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        course = _make_course(
            progress_pct=75,
            completed_lessons=2,
            total_lessons=4,
            failed_lessons=1,
            skipped_lessons=0,
        )
        mock_course_manager.get_course.return_value = course

        resp = await client.get("/api/courses/course-001/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {
            "id": "course-001",
            "status": "completed",
            "progress_pct": 75,
            "total_lessons": 4,
            "completed_lessons": 2,
            "failed_lessons": 1,
            "skipped_lessons": 0,
            "pending_lessons": 1,  # 4 - 2 - 1 - 0
        }

    async def test_get_progress_pending_never_negative(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """pending_lessons should be max(0, ...) even if counters exceed total."""
        course = _make_course(
            total_lessons=2,
            completed_lessons=2,
            failed_lessons=1,
            skipped_lessons=0,
        )
        mock_course_manager.get_course.return_value = course

        resp = await client.get("/api/courses/course-001/progress")
        assert resp.status_code == 200
        assert resp.json()["pending_lessons"] == 0

    async def test_get_progress_returns_404_when_missing(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.get_course.return_value = None

        resp = await client.get("/api/courses/no-such/progress")
        assert resp.status_code == 404


class TestCallback:
    async def test_callback_processes_payload(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        resp = await client.post(
            "/api/courses/course-001/callback",
            json={
                "job_id": "job-001",
                "status": "completed",
                "video_url": "https://cdn.example.com/v.mp4",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "received"}

        # Verify EXACT parameters passed to handle_callback
        mock_course_manager.handle_callback.assert_awaited_once()
        call_args = mock_course_manager.handle_callback.call_args
        assert call_args.args[0] == "course-001"
        assert call_args.kwargs["job_id"] == "job-001"
        assert call_args.kwargs["status"] == "completed"
        assert call_args.kwargs["video_url"] == "https://cdn.example.com/v.mp4"
        assert call_args.kwargs["error"] is None
        assert call_args.kwargs["artifacts"] is None

    async def test_callback_with_error_and_artifacts(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        resp = await client.post(
            "/api/courses/course-XYZ/callback",
            json={
                "job_id": "job-err",
                "status": "failed",
                "error": "Render timeout",
                "artifacts": {"log": "some-log-url"},
            },
        )
        assert resp.status_code == 200

        call_kwargs = mock_course_manager.handle_callback.call_args.kwargs
        assert call_kwargs["job_id"] == "job-err"
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["error"] == "Render timeout"
        assert call_kwargs["artifacts"] == {"log": "some-log-url"}
        assert call_kwargs["video_url"] is None

    async def test_callback_missing_required_fields_returns_422(
        self,
        client: AsyncClient,
    ) -> None:
        """job_id and status are required in CallbackPayload."""
        resp = await client.post(
            "/api/courses/course-001/callback",
            json={"video_url": "https://cdn.example.com/v.mp4"},
        )
        assert resp.status_code == 422

    async def test_callback_extra_fields_ignored(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """Extra fields not in CallbackPayload schema should be silently ignored."""
        resp = await client.post(
            "/api/courses/course-001/callback",
            json={
                "job_id": "job-extra",
                "status": "completed",
                "unexpected_field": "should be ignored",
            },
        )
        assert resp.status_code == 200
        call_kwargs = mock_course_manager.handle_callback.call_args.kwargs
        assert call_kwargs["job_id"] == "job-extra"
        assert call_kwargs["status"] == "completed"


class TestDeleteCourse:
    async def test_delete_returns_404_when_missing(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.delete_course.return_value = False

        resp = await client.delete("/api/courses/no-such")
        assert resp.status_code == 404
        assert resp.json()["error"]["message"] == "Course not found"

    async def test_delete_returns_success(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.delete_course.return_value = True

        resp = await client.delete("/api/courses/course-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"status": "deleted", "id": "course-001"}

    async def test_delete_passes_course_id_to_manager(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.delete_course.return_value = True

        await client.delete("/api/courses/course-xyz")
        mock_course_manager.delete_course.assert_awaited_once_with(
            "course-xyz", tenant_id="default"
        )


class TestGetBundle:
    async def test_bundle_returns_url_when_present(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        course = _make_course(bundle_url="https://cdn.example.com/bundle.zip")
        mock_course_manager.get_course.return_value = course

        resp = await client.get("/api/courses/course-001/bundle")
        assert resp.status_code == 200
        assert resp.json() == {
            "id": "course-001",
            "bundle_url": "https://cdn.example.com/bundle.zip",
        }

    async def test_bundle_returns_null_when_not_ready(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        course = _make_course(bundle_url=None)
        mock_course_manager.get_course.return_value = course

        resp = await client.get("/api/courses/course-001/bundle")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bundle_url"] is None
        assert data["id"] == "course-001"
        assert "not yet assembled" in data["message"].lower()

    async def test_bundle_returns_404_when_course_missing(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.get_course.return_value = None

        resp = await client.get("/api/courses/nonexistent/bundle")
        assert resp.status_code == 404


class TestListCourses:
    async def test_list_courses_returns_all_fields(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """Verify every field in the paginated response."""
        course = _make_course()
        mock_course_manager.list_courses.return_value = ([course], 42)

        resp = await client.get("/api/courses?offset=20&limit=1")
        assert resp.status_code == 200
        data = resp.json()

        # Pagination metadata
        assert data["total"] == 42
        assert data["limit"] == 1
        assert data["offset"] == 20

        # Course list
        assert len(data["courses"]) == 1
        c = data["courses"][0]
        assert c == {
            "id": "course-001",
            "tenant_id": "default",
            "status": "completed",
            "title": "Test Course",
            "description": "A test course",
            "progress_pct": 100,
            "total_lessons": 3,
            "completed_lessons": 3,
            "failed_lessons": 0,
            "skipped_lessons": 0,
        }

    async def test_list_courses_passes_pagination_to_manager(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """Verify limit and offset are forwarded to manager.list_courses."""
        mock_course_manager.list_courses.return_value = ([], 0)

        await client.get("/api/courses?offset=10&limit=5")
        call_kwargs = mock_course_manager.list_courses.call_args
        assert call_kwargs.kwargs["limit"] == 5
        assert call_kwargs.kwargs["offset"] == 10

    async def test_list_courses_default_pagination(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        """Without query params, defaults are limit=20, offset=0."""
        mock_course_manager.list_courses.return_value = ([], 0)

        resp = await client.get("/api/courses")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 20
        assert data["offset"] == 0

        call_kwargs = mock_course_manager.list_courses.call_args
        assert call_kwargs.kwargs["limit"] == 20
        assert call_kwargs.kwargs["offset"] == 0

    async def test_list_courses_empty(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.list_courses.return_value = ([], 0)

        resp = await client.get("/api/courses")
        assert resp.status_code == 200
        data = resp.json()
        assert data["courses"] == []
        assert data["total"] == 0


class TestGetManifest:
    async def test_manifest_returns_data(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        course = _make_course(manifest={"modules": [{"title": "M1"}]})
        mock_course_manager.get_course.return_value = course

        resp = await client.get("/api/courses/course-001/manifest")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "course-001"
        assert data["manifest"] == {"modules": [{"title": "M1"}]}

    async def test_manifest_returns_404_when_missing(
        self,
        client: AsyncClient,
        mock_course_manager: AsyncMock,
    ) -> None:
        mock_course_manager.get_course.return_value = None

        resp = await client.get("/api/courses/nonexistent/manifest")
        assert resp.status_code == 404
        assert resp.json()["error"]["message"] == "Course not found"
