"""Tests for health check endpoints."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from httpx._transports.asgi import _ASGIApp
from sqlalchemy.exc import OperationalError

from src.api.health import router


@pytest.fixture
def app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(router)

    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock()
    mock_engine.connect = MagicMock(return_value=mock_ctx)

    test_app.state.db_engine = mock_engine
    test_app.state.redis_client = None
    return test_app


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[AsyncClient]:
    transport = ASGITransport(app=cast(_ASGIApp, app))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthCheck:
    async def test_healthy(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

        # Verify all component statuses individually
        assert data["components"]["api"] == "healthy"
        assert data["components"]["database"] == "healthy"
        # redis_client is None so it should be "not configured" (still counts as healthy)
        assert data["components"]["redis"] in ("healthy", "not configured")

    async def test_redis_not_configured(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        data = resp.json()
        assert data["components"]["redis"] == "not configured"
        # Overall status should still be "healthy" when redis is not configured
        assert data["status"] == "healthy"
        assert resp.status_code == 200

    async def test_with_redis_healthy(self, app: FastAPI) -> None:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        app.state.redis_client = mock_redis

        transport = ASGITransport(app=cast(_ASGIApp, app))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health")
        data = resp.json()
        assert data["components"]["redis"] == "healthy"
        assert data["status"] == "healthy"
        assert resp.status_code == 200

    async def test_with_redis_unhealthy(self, app: FastAPI) -> None:
        """Lines 34-35: Redis ping failure is reported as unhealthy."""
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=ConnectionError("Redis timeout"))
        app.state.redis_client = mock_redis

        transport = ASGITransport(app=cast(_ASGIApp, app))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health")
        data = resp.json()
        assert "unhealthy" in data["components"]["redis"]
        assert data["status"] == "degraded"
        assert resp.status_code == 503

    async def test_db_unhealthy(self, app: FastAPI) -> None:
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = OperationalError("DB down", {}, Exception("DB down"))
        app.state.db_engine = mock_engine

        transport = ASGITransport(app=cast(_ASGIApp, app))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health")
        data = resp.json()
        assert "unhealthy" in data["components"]["database"]
        assert data["status"] == "degraded"
        assert resp.status_code == 503

    async def test_components_has_all_expected_keys(self, client: AsyncClient) -> None:
        """Verify the components dict always contains database, redis, and api."""
        resp = await client.get("/health")
        data = resp.json()
        components = data["components"]
        assert "database" in components
        assert "redis" in components
        assert "api" in components


class TestLive:
    async def test_liveness(self, client: AsyncClient) -> None:
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_live_endpoint_returns_correct_body(self, client: AsyncClient) -> None:
        """Verify /health/live returns exactly {"status": "ok"} with no extra keys."""
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"status": "ok"}
        assert len(body) == 1


class TestReady:
    async def test_ready_healthy(self, client: AsyncClient) -> None:
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["status"] == "healthy"

    async def test_ready_db_down(self, app: FastAPI) -> None:
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = OperationalError("conn fail", {}, Exception("conn fail"))
        app.state.db_engine = mock_engine

        transport = ASGITransport(app=cast(_ASGIApp, app))
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/health/ready")
        assert resp.status_code == 503
        data = resp.json()
        assert data["ready"] is False
        assert data["status"] == "unhealthy"
