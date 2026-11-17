"""Health check endpoints."""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", operation_id="health_check")
async def health(request: Request) -> JSONResponse:
    """Composite health check with component details."""
    components: dict[str, str] = {"api": "healthy"}

    # Database
    try:
        engine = request.app.state.db_engine
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        components["database"] = "healthy"
    except (OSError, SQLAlchemyError) as e:
        components["database"] = f"unhealthy: {e}"

    # Redis
    redis = getattr(request.app.state, "redis_client", None)
    if redis:
        try:
            await redis.ping()
            components["redis"] = "healthy"
        except (OSError, RedisError) as e:
            components["redis"] = f"unhealthy: {e}"
    else:
        components["redis"] = "not configured"

    all_ok = all(v in ("healthy", "not configured") for v in components.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "healthy" if all_ok else "degraded", "components": components},
    )


@router.get("/health/ready", operation_id="health_ready")
async def readiness(request: Request) -> JSONResponse:
    """Readiness probe — checks database connectivity."""
    try:
        engine = request.app.state.db_engine
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(status_code=200, content={"status": "healthy", "ready": True})
    except (OSError, SQLAlchemyError) as e:
        return JSONResponse(
            status_code=503, content={"status": "unhealthy", "ready": False, "error": str(e)}
        )


@router.get("/health/live", operation_id="health_live")
async def liveness() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/healthz", operation_id="health.live")
async def healthz() -> dict:
    """Modern K8s liveness probe path (alias of /health/live)."""
    return await liveness()


@router.get("/readyz", operation_id="health.ready")
async def readyz(request: Request) -> JSONResponse:
    """Modern K8s readiness probe path (alias of /health/ready)."""
    return await readiness(request)
