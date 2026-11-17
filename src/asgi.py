"""ASGI application for course_service.

Long-form course generation microservice: curriculum planning,
lesson dispatch to orchestrator, and course assembly.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from w2v_common.asgi import register_error_envelope_handlers, register_middleware

from src.config import get_settings
from src.logging_config import configure_logging

load_dotenv()

settings = get_settings()
configure_logging(json_logs=settings.logging.json_logs, level=settings.logging.level)
logger = logging.getLogger(__name__)


# ============================================================================
# Health Check Logging Filter
# ============================================================================


class HealthCheckLoggingFilter(logging.Filter):
    """Suppress noisy health-check requests from uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for health-check paths to suppress their log lines."""
        message = record.getMessage()
        return not any(
            path in message
            for path in ["/health", "/health/ready", "/health/live", "/healthz", "/readyz"]
        )


uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(HealthCheckLoggingFilter())


# ============================================================================
# Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan -- init DB, Redis, and all services."""
    logger.info("=" * 70)
    logger.info("Starting Course Service v%s", settings.app_version)
    logger.info("=" * 70)

    # ---- Database ----
    from src.infra.database import create_engine_with_retry, create_session_factory

    engine = await create_engine_with_retry(
        settings.database.url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_timeout=settings.database.pool_timeout,
        pool_recycle=settings.database.pool_recycle,
        echo=settings.database.echo,
    )
    app.state.db_engine = engine

    # Schema is owned by alembic -- the gitops Deployment runs
    # ``alembic upgrade head`` in an initContainer before this app
    # starts (see gitops-production
    # clusters/strato/services/w2v/deployments.yaml). Do NOT call
    # ``Base.metadata.create_all`` here: it bypasses migration history
    # and can drift the live schema away from what alembic expects.
    logger.info("Database connected (schema managed by alembic)")

    session_factory = create_session_factory(engine)
    app.state.session_factory = session_factory

    # ---- Redis ----
    from src.infra.redis_client import RedisClient

    redis_client: RedisClient | None = None
    try:
        redis_client = RedisClient(
            settings.redis.url,
            max_connections=settings.redis.max_connections,
            socket_timeout=settings.redis.socket_timeout,
            socket_connect_timeout=settings.redis.socket_connect_timeout,
            decode_responses=settings.redis.decode_responses,
        )
        await redis_client.connect()
        app.state.redis_client = redis_client
        logger.info("Redis connected: %s", settings.redis.url)
    except (OSError, RedisError) as e:
        logger.warning("Redis unavailable: %s -- continuing without caching", e)
        app.state.redis_client = None

    # ---- HTTP Clients ----
    from w2v_common.discovery import service_url

    from src.clients.analysis_client import AnalysisClient
    from src.clients.orchestrator_client import OrchestratorClient
    from src.clients.slide_client import SlideClient

    orchestrator_client = OrchestratorClient(base_url=service_url("orchestrator"))
    analysis_client = AnalysisClient(base_url=service_url("analysis"))
    slide_client = SlideClient(base_url=service_url("slide_service"))

    # ---- Curriculum Planner ----
    # LLM calls go through worker_management (the sole LLM gateway). The
    # WMClient picks up WORKER_MANAGEMENT_URL + DOWNSTREAM_LICENSE_TOKEN
    # from the environment; per-request model overrides flow through
    # CourseConfig.llm_model from the SPA, not configured here.
    from src.services.curriculum.planner import CurriculumPlanner

    planner = CurriculumPlanner()

    # ---- Course Manager ----
    from src.database.repository import CourseRepository
    from src.services.course_manager import CourseManager

    repository = CourseRepository(session_factory)
    course_manager = CourseManager(
        repository=repository,
        analysis_client=analysis_client,
        orchestrator_client=orchestrator_client,
        planner=planner,
        slide_client=slide_client,
        concurrency_limit=settings.course.default_concurrency_limit,
        max_retries=settings.course.default_max_retries,
        lesson_timeout=1800.0,
        callback_base_url=settings.course.callback_base_url or None,
    )
    app.state.course_manager = course_manager

    # ---- Startup recovery for incomplete courses ----
    try:
        incomplete = await repository.list_incomplete_courses()
        if incomplete:
            logger.info("Recovering %d incomplete course(s)", len(incomplete))
            if not hasattr(app.state, "background_tasks"):
                app.state.background_tasks = set()
            for course in incomplete:
                # Use the course's stored tenant_id — recovery runs outside
                # any request scope and must respect the original ownership.
                task = asyncio.create_task(
                    course_manager.run_pipeline(course.course_id, tenant_id=course.tenant_id),
                    name=f"recovery-{course.course_id}",
                )
                app.state.background_tasks.add(task)
                task.add_done_callback(app.state.background_tasks.discard)
                logger.info(
                    "Recovery started: course=%s status=%s", course.course_id, course.status
                )
    except (OSError, SQLAlchemyError, ValueError, RuntimeError) as exc:
        logger.error("Startup recovery failed: %s", exc, exc_info=True)

    logger.info("=" * 70)
    logger.info("Course Service started successfully")
    logger.info(
        "Environment: %s | API: %s:%d", settings.environment, settings.api.host, settings.api.port
    )
    logger.info("=" * 70)

    yield

    # ---- Shutdown ----
    logger.info("Shutting down Course Service")

    # Cancel and await any in-flight recovery / pipeline tasks BEFORE closing
    # the HTTP clients and DB engine they depend on. Without this, a pod
    # eviction during a long-running pipeline destroys the task mid-write,
    # producing "Task was destroyed but it is pending" in logs and leaving
    # courses stuck in non-terminal states — the next pod's recovery loop
    # then re-picks them up indefinitely.
    pending = list(getattr(app.state, "background_tasks", ()))
    if pending:
        logger.info("Cancelling %d in-flight background task(s)", len(pending))
        for task in pending:
            task.cancel()
        # Wait for cooperative cancellation handlers to run; swallow CancelledError
        # and any per-task exceptions (they're already in the task's own except path).
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info("All background tasks settled")

    await orchestrator_client.close()
    await analysis_client.close()
    await slide_client.close()

    if redis_client:
        try:
            await redis_client.disconnect()
        except (OSError, RedisError) as e:
            logger.error("Error closing Redis: %s", e)

    await engine.dispose()
    logger.info("Course Service shutdown complete")


# ============================================================================
# Public-endpoint policy (kept in sync with w2v_common.auth.middleware)
# ============================================================================
# Used only by custom_openapi() below to decide which paths advertise the
# Bearer security requirement in the schema. The actual auth enforcement is
# done by w2v_common.auth.middleware.AuthMiddleware.
_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/health/live",
        "/health/ready",
        "/healthz",
        "/readyz",
        "/ready",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.ico",
        "/metrics",
    }
)
_PUBLIC_PREFIXES: tuple[str, ...] = ("/docs", "/redoc", "/health/")


# ============================================================================
# Create App
# ============================================================================

app = FastAPI(
    title=settings.app_name,
    description=(
        "Long-form course generation microservice: "
        "curriculum planning, lesson dispatch, and course assembly"
    ),
    version=settings.app_version,
    lifespan=lifespan,
    redirect_slashes=False,
    swagger_ui_parameters={"persistAuthorization": True},
    # Fallback unique-id generator. Every route still sets `operation_id=`
    # explicitly per OPERATION_ID_PLAN.md; this exists so a future route
    # that forgets the kwarg degrades to `<tag>_<func>` instead of FastAPI's
    # default `<func>_<path>_<method>` (garbage in generated clients).
    generate_unique_id_function=lambda route: (
        f"{route.tags[0] if route.tags else 'default'}_{route.name}"
    ),
    openapi_tags=[
        {"name": "health", "description": "Liveness, readiness, dependency probes."},
        {
            "name": "courses",
            "description": (
                "Course lifecycle: creation, listing, detail, deletion, "
                "progress, orchestrator callbacks, bundle + manifest retrieval."
            ),
        },
    ],
)

# CORS
register_middleware(
    app,
    CORSMiddleware,
    allow_origins=settings.api.cors_origins,
    allow_credentials=settings.api.cors_allow_credentials,
    allow_methods=settings.api.cors_allow_methods,
    allow_headers=settings.api.cors_allow_headers,
)

# Auth middleware
from src.auth.middleware import AuthMiddleware  # noqa: E402 -- must import after app initialization

if settings.auth.enabled:
    app.add_middleware(AuthMiddleware)
    logger.info("Authentication middleware enabled")
else:
    logger.warning("Running WITHOUT authentication -- development mode only!")


# Request ID middleware — registered last so it is the OUTERMOST layer and runs first
# inbound, binding the request_id ContextVar before auth/handlers execute.
from src.middleware.request_id import RequestIdMiddleware  # noqa: E402

register_middleware(app, RequestIdMiddleware)


# ============================================================================
# Root
# ============================================================================


@app.get(
    "/",
    operation_id="service_root",
    tags=["health"],
    summary="Service banner + endpoint index",
)
async def root() -> dict[str, Any]:
    """Return service info and available endpoint listing."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": (
            "Long-form course generation microservice: "
            "curriculum planning, lesson dispatch, and course assembly"
        ),
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "courses": "/api/courses",
            "course_detail": "/api/courses/{id}",
            "modules": "/api/courses/{id}/modules",
            "lessons": "/api/courses/{id}/lessons/{lid}",
            "progress": "/api/courses/{id}/progress",
            "manifest": "/api/courses/{id}/manifest",
        },
        "status": "running",
        "environment": settings.environment,
    }


# Mount routers
from src.api.health import (  # noqa: E402 -- must import after app initialization
    router as health_router,
)
from src.api.routes import (  # noqa: E402 -- must import after app initialization
    router as course_router,
)

app.include_router(health_router)
app.include_router(course_router)


# ============================================================================
# OpenAPI customisation — BearerAuth scheme + TenantIdHeader parameter
# ============================================================================
#
# Mirrors the kag / worker_management / nlp_service / analysis_service /
# video_compiler_service / video_analyzer_service pattern: every protected
# operation advertises ``security: [{BearerAuth: []}]`` and a reusable
# ``TenantIdHeader`` parameter so Swagger UI exposes the license-token
# transport correctly. The actual enforcement lives in
# ``w2v_common.auth.middleware.AuthMiddleware``.
def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    from fastapi.openapi.utils import get_openapi

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )

    components = schema.setdefault("components", {})
    components.setdefault("securitySchemes", {})["BearerAuth"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "License",
        "description": (
            "License token. Transport: `X-Tenant-Id: Bearer <token>` "
            "(not the Authorization header — that is reserved for caller-side auth)."
        ),
    }
    components.setdefault("parameters", {})["TenantIdHeader"] = {
        "name": "X-Tenant-Id",
        "in": "header",
        "required": False,
        "description": "License token transport: `Bearer <token>`.",
        "schema": {"type": "string"},
    }

    for path, item in schema.get("paths", {}).items():
        clean = path.rstrip("/") or "/"
        is_public = clean in _PUBLIC_PATHS or any(clean.startswith(p) for p in _PUBLIC_PREFIXES)
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            if is_public:
                operation.pop("security", None)
            else:
                operation.setdefault("security", [{"BearerAuth": []}])
            params = operation.setdefault("parameters", [])
            if not any(
                p.get("name", "").lower() == "x-tenant-id" and p.get("in") == "header"
                for p in params
            ):
                params.append({"$ref": "#/components/parameters/TenantIdHeader"})

    app.openapi_schema = schema
    return app.openapi_schema


object.__setattr__(app, "openapi", custom_openapi)


# ============================================================================
# Error Handlers — canonical fleet error envelope (shape #2), from w2v_common
# ============================================================================
register_error_envelope_handlers(app, debug=settings.api.debug)


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point for running the ASGI application."""
    import uvicorn

    uvicorn.run(
        "src.asgi:app",
        host=settings.api.host,
        port=settings.api.port,
        log_level=settings.logging.level.lower(),
        reload=settings.api.reload,
    )


if __name__ == "__main__":
    main()
