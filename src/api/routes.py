"""Course service REST API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.api.schemas import CallbackPayload, CreateCourseRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["courses"])


def _get_manager(request: Request) -> Any:
    return request.app.state.course_manager


def _get_tenant_id(request: Request) -> str:
    """Extract tenant_id set by auth middleware, fall back to default."""
    return getattr(request.state, "tenant_id", None) or "default"


@router.post(
    "/courses",
    operation_id="create_course",
    responses={
        422: {"description": "Validation error"},
    },
)
async def create_course(body: CreateCourseRequest, request: Request) -> dict:
    """Create a new course from source material."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    course = await manager.create_course(
        tenant_id=tenant_id,
        source_type=body.source_type,
        source_url=body.source_url,
        source_documents=body.source_documents,
        config=body.config.model_dump() if body.config else {},
    )

    # Fire off the pipeline in the background — tenant_id is captured in the
    # task closure so the background pipeline cannot read/write any other
    # tenant's course even though it runs outside the request context.
    import asyncio

    task = asyncio.create_task(
        manager.run_pipeline(course.course_id, tenant_id=tenant_id, user_context=body.user_context),
        name=f"course-pipeline-{course.course_id}",
    )
    if not hasattr(request.app.state, "background_tasks"):
        request.app.state.background_tasks = set()
    request.app.state.background_tasks.add(task)
    task.add_done_callback(request.app.state.background_tasks.discard)

    return {
        "id": course.course_id,
        "status": course.status,
        "message": "Course creation started",
    }


@router.get("/courses", operation_id="list_courses")
async def list_courses(
    request: Request,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """List courses (paginated)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    courses, total = await manager.list_courses(tenant_id, limit=limit, offset=offset)
    return {
        "courses": [
            {
                "id": c.course_id,
                "tenant_id": c.tenant_id,
                "status": c.status,
                "title": c.title,
                "description": c.description,
                "progress_pct": c.progress_pct,
                "total_lessons": c.total_lessons,
                "completed_lessons": c.completed_lessons,
                "failed_lessons": c.failed_lessons,
                "skipped_lessons": c.skipped_lessons,
            }
            for c in courses
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/courses/{course_id}",
    operation_id="get_course",
    responses={
        404: {"description": "Course not found"},
    },
)
async def get_course(course_id: str, request: Request) -> dict:
    """Get course detail (tenant-scoped)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    course = await manager.get_course(course_id, tenant_id=tenant_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    modules = await manager.get_modules(course_id, tenant_id=tenant_id)
    lessons = await manager.get_lessons(course_id, tenant_id=tenant_id)
    return {
        "id": course.course_id,
        "tenant_id": course.tenant_id,
        "status": course.status,
        "title": course.title,
        "description": course.description,
        "progress_pct": course.progress_pct,
        "total_lessons": course.total_lessons,
        "completed_lessons": course.completed_lessons,
        "failed_lessons": course.failed_lessons,
        "skipped_lessons": course.skipped_lessons,
        "bundle_url": course.bundle_url,
        "error": course.error,
        "config": course.config or {},
        "created_at": course.created_at.isoformat() if course.created_at else None,
        "updated_at": course.updated_at.isoformat() if course.updated_at else None,
        "modules": [
            {"module_id": m.module_id, "title": m.title, "status": m.status, "position": m.position}
            for m in modules
        ],
        "lessons": [
            {"lesson_id": ln.lesson_id, "title": ln.title, "pipeline_status": ln.pipeline_status}
            for ln in lessons
        ],
    }


@router.delete(
    "/courses/{course_id}",
    operation_id="delete_course",
    responses={
        404: {"description": "Course not found"},
    },
)
async def delete_course(course_id: str, request: Request) -> dict:
    """Cancel / delete a course (tenant-scoped)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    deleted = await manager.delete_course(course_id, tenant_id=tenant_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Course not found")
    return {"status": "deleted", "id": course_id}


@router.get(
    "/courses/{course_id}/progress",
    operation_id="get_course_progress",
    responses={
        404: {"description": "Course not found"},
    },
)
async def get_progress(course_id: str, request: Request) -> dict:
    """Get course progress summary (tenant-scoped)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    course = await manager.get_course(course_id, tenant_id=tenant_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    pending = (
        course.total_lessons
        - course.completed_lessons
        - course.failed_lessons
        - course.skipped_lessons
    )
    return {
        "id": course.course_id,
        "status": course.status,
        "progress_pct": course.progress_pct,
        "total_lessons": course.total_lessons,
        "completed_lessons": course.completed_lessons,
        "failed_lessons": course.failed_lessons,
        "skipped_lessons": course.skipped_lessons,
        "pending_lessons": max(0, pending),
    }


@router.post(
    "/courses/{course_id}/callback",
    operation_id="receive_course_lesson_callback",
    responses={
        422: {"description": "Validation error"},
    },
)
async def callback(course_id: str, body: CallbackPayload, request: Request) -> dict:
    """Receive orchestrator webhook callback for a lesson pipeline job.

    The forwarded ``X-Tenant-Id`` is matched against the course owner; a
    callback for a course this tenant does not own is reported as 404 to
    avoid confirming the course's existence.
    """
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    applied = await manager.handle_callback(
        course_id,
        tenant_id=tenant_id,
        job_id=body.job_id,
        status=body.status,
        video_url=body.video_url,
        error=body.error,
        artifacts=body.artifacts,
    )
    if not applied:
        raise HTTPException(status_code=404, detail="Course not found")
    return {"status": "received"}


@router.get(
    "/courses/{course_id}/bundle",
    operation_id="get_course_bundle",
    responses={
        404: {"description": "Course not found"},
    },
)
async def get_bundle(course_id: str, request: Request) -> dict:
    """Get course bundle download URL (tenant-scoped)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    course = await manager.get_course(course_id, tenant_id=tenant_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    if course.bundle_url:
        return {"id": course_id, "bundle_url": course.bundle_url}
    return {"id": course_id, "bundle_url": None, "message": "Bundle not yet assembled"}


@router.get(
    "/courses/{course_id}/manifest",
    operation_id="get_course_manifest",
    responses={
        404: {"description": "Course not found"},
    },
)
async def get_manifest(course_id: str, request: Request) -> dict:
    """Get course manifest JSON (tenant-scoped)."""
    manager = _get_manager(request)
    tenant_id = _get_tenant_id(request)
    course = await manager.get_course(course_id, tenant_id=tenant_id)
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return {"id": course_id, "manifest": course.manifest}
