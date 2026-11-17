"""Tests for clients/base.py — HTTP retry and poll logic."""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.base import BaseServiceClient


@pytest.fixture
def client() -> BaseServiceClient:
    return BaseServiceClient(
        base_url="http://localhost:8000",
        service_name="test-svc",
        timeout=5.0,
    )


def _make_response(
    status_code: int = 200,
    json_data: dict | None = None,
    *,
    raise_for_status_error: Exception | None = None,
) -> MagicMock:
    """Build a mock httpx.Response with consistent behaviour."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_for_status_error:
        resp.raise_for_status.side_effect = raise_for_status_error
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _mock_async_client(**overrides: object) -> AsyncMock:
    """Return an AsyncMock that quacks like httpx.AsyncClient."""
    mock = AsyncMock(spec=httpx.AsyncClient)
    mock.is_closed = False
    for key, value in overrides.items():
        setattr(mock, key, value)
    return mock


class TestInit:
    def test_strips_trailing_slash(self) -> None:
        c = BaseServiceClient("http://localhost:8000/", "svc")
        assert c.base_url == "http://localhost:8000"

    def test_stores_license_token(self) -> None:
        c = BaseServiceClient("http://x", "svc", license_token="tok-123")
        assert c.license_token == "tok-123"


class TestGetClient:
    async def test_creates_client_lazily(self, client: BaseServiceClient) -> None:
        http_client = await client._get_client()
        assert http_client is not None
        assert not http_client.is_closed
        await client.close()

    async def test_reuses_existing_client(self, client: BaseServiceClient) -> None:
        c1 = await client._get_client()
        c2 = await client._get_client()
        assert c1 is c2
        await client.close()

    async def test_recreates_after_close(self, client: BaseServiceClient) -> None:
        c1 = await client._get_client()
        await client.close()
        c2 = await client._get_client()
        assert c1 is not c2
        await client.close()

    async def test_jwt_token_uses_authorization_header(self) -> None:
        # JWT-shaped tokens (header.payload.signature) route via Authorization.
        c = BaseServiceClient("http://x", "svc", license_token="header.payload.signature")
        http_client = await c._get_client()
        assert http_client.headers.get("Authorization") == "Bearer header.payload.signature"
        assert http_client.headers.get("X-Tenant-Id") is None
        await c.close()

    async def test_opaque_token_uses_x_tenant_id_header(self) -> None:
        # Opaque license tokens route via X-Tenant-Id: Bearer <token> so the
        # license-authz envoy ext_authz can introspect them. See
        # w2v_common.clients.base for the routing rationale.
        c = BaseServiceClient("http://x", "svc", license_token="bearer-tok")
        http_client = await c._get_client()
        assert http_client.headers.get("X-Tenant-Id") == "Bearer bearer-tok"
        assert http_client.headers.get("Authorization") is None
        await c.close()


class TestClose:
    async def test_close_when_not_connected(self, client: BaseServiceClient) -> None:
        await client.close()  # No crash

    async def test_close_sets_client_none(self, client: BaseServiceClient) -> None:
        await client._get_client()
        await client.close()
        assert client._client is None


class TestHealthCheck:
    async def test_healthy_service(self, client: BaseServiceClient) -> None:
        """Verify health_check calls GET /health and merges upstream JSON."""
        mock_resp = _make_response(200, {"uptime": 100})
        mock_http = _mock_async_client(get=AsyncMock(return_value=mock_resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client.health_check()

        # Verify correct endpoint was called
        mock_http.get.assert_awaited_once_with("/health")
        # Verify raise_for_status was invoked
        mock_resp.raise_for_status.assert_called_once()
        # Verify returned dict has correct shape and values
        assert result["status"] == "healthy"
        assert result["service"] == "test-svc"
        assert result["uptime"] == 100

    async def test_unhealthy_service(self, client: BaseServiceClient) -> None:
        """Verify health_check returns unhealthy dict on connection error."""
        mock_http = _mock_async_client(
            get=AsyncMock(side_effect=httpx.ConnectError("refused")),
        )

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client.health_check()

        assert result["status"] == "unhealthy"
        assert result["service"] == "test-svc"
        assert "refused" in result["error"]

    async def test_health_check_returns_unhealthy_on_non_200(
        self, client: BaseServiceClient
    ) -> None:
        """Verify health_check returns unhealthy when upstream returns HTTP 503."""
        exc = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=MagicMock(status_code=503),
        )
        mock_resp = _make_response(503, raise_for_status_error=exc)
        mock_http = _mock_async_client(get=AsyncMock(return_value=mock_resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client.health_check()

        assert result["status"] == "unhealthy"
        assert result["service"] == "test-svc"
        assert "503" in result["error"]


class TestRequest:
    async def test_successful_request(self, client: BaseServiceClient) -> None:
        """Verify _request returns parsed JSON and calls the correct HTTP method/path."""
        mock_resp = _make_response(200, {"result": "ok"})
        mock_http = _mock_async_client(request=AsyncMock(return_value=mock_resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client._request("GET", "/test")

        assert result == {"result": "ok"}
        mock_http.request.assert_awaited_once_with(
            "GET", "/test", json=None, params=None, headers=None
        )
        # The success path checks status_code directly and never calls the bare
        # response.raise_for_status() (which would drop the body); errors are
        # raised body-preserving via raise_for_status_with_body. See w2v_common
        # clients.base _request (>=0.18.8).
        mock_resp.raise_for_status.assert_not_called()

    async def test_request_passes_json_and_params(self, client: BaseServiceClient) -> None:
        """Verify _request forwards json and params to the underlying client."""
        mock_resp = _make_response(200, {"created": True})
        mock_http = _mock_async_client(request=AsyncMock(return_value=mock_resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client._request(
                "POST", "/items", json={"name": "x"}, params={"dry": "1"}
            )

        assert result == {"created": True}
        mock_http.request.assert_awaited_once_with(
            "POST", "/items", json={"name": "x"}, params={"dry": "1"}, headers=None
        )

    async def test_retries_on_500(self, client: BaseServiceClient) -> None:
        """Verify a 500 triggers retry and the successful response is returned.

        Uses GET because w2v_common 0.16.3 restricts retries to RFC 9110
        idempotent methods (GET/HEAD/OPTIONS/PUT/DELETE) unless the caller
        explicitly opts in via an Idempotency-Key.
        """
        error_resp = _make_response(500)
        ok_resp = _make_response(200, {"ok": True})

        mock_http = _mock_async_client(
            request=AsyncMock(side_effect=[error_resp, ok_resp]),
        )

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            result = await client._request("GET", "/retry")

        assert result == {"ok": True}
        assert mock_http.request.await_count == 2
        # Verify sleep was called for backoff (attempt=1 → delay=1*1)
        mock_sleep.assert_awaited_once_with(1.0)

    async def test_retries_on_transport_error(self, client: BaseServiceClient) -> None:
        """Verify a transport error triggers retry and eventual success is returned."""
        ok_resp = _make_response(200, {"ok": True})
        mock_http = _mock_async_client(
            request=AsyncMock(side_effect=[httpx.ConnectError("fail"), ok_resp]),
        )

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await client._request("GET", "/endpoint")

        assert result == {"ok": True}
        assert mock_http.request.await_count == 2

    async def test_raises_after_max_retries(self, client: BaseServiceClient) -> None:
        """Verify exhausted retries raise the underlying transport error."""
        mock_http = _mock_async_client(
            request=AsyncMock(side_effect=httpx.ConnectError("always fail")),
        )

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(httpx.ConnectError, match="always fail"),
        ):
            await client._request("GET", "/fail")

        # Verify all 3 attempts were made
        assert mock_http.request.await_count == 3

    async def test_request_includes_auth_header(self) -> None:
        """Opaque license tokens route via X-Tenant-Id (license-authz contract)."""
        authed = BaseServiceClient("http://localhost:8000", "svc", license_token="my-secret-token")
        http_client = await authed._get_client()
        assert http_client.headers["X-Tenant-Id"] == "Bearer my-secret-token"
        assert "Authorization" not in http_client.headers
        await authed.close()

    async def test_request_json_parsing(self, client: BaseServiceClient) -> None:
        """Verify _request correctly parses complex nested JSON."""
        nested = {"data": {"items": [1, 2, 3], "meta": {"page": 1}}}
        mock_resp = _make_response(200, nested)
        mock_http = _mock_async_client(request=AsyncMock(return_value=mock_resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client._request("GET", "/nested")

        assert result["data"]["items"] == [1, 2, 3]
        assert result["data"]["meta"]["page"] == 1


class TestPollJob:
    async def test_poll_completes(self, client: BaseServiceClient) -> None:
        """Verify poll_job returns the completed result after pending status."""
        pending_resp = _make_response(200, {"status": "processing"})
        done_resp = _make_response(200, {"status": "completed", "video_url": "https://x"})

        mock_http = _mock_async_client(
            request=AsyncMock(side_effect=[pending_resp, done_resp]),
        )

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await client.poll_job("/api/jobs", "job-1", interval=0.01, timeout=10)

        assert result["status"] == "completed"
        assert result["video_url"] == "https://x"
        # Verify the GET endpoint includes the job_id
        first_call = mock_http.request.call_args_list[0]
        assert first_call.args == ("GET", "/api/jobs/job-1")

    async def test_poll_raises_on_failure(self, client: BaseServiceClient) -> None:
        """Verify poll_job raises RuntimeError with error detail on failed status."""
        failed_resp = _make_response(200, {"status": "failed", "error": "boom"})
        mock_http = _mock_async_client(request=AsyncMock(return_value=failed_resp))

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            pytest.raises(RuntimeError, match="boom"),
        ):
            await client.poll_job("/api/jobs", "job-1", interval=0.01)

    async def test_poll_raises_on_error_status(self, client: BaseServiceClient) -> None:
        """Verify poll_job raises RuntimeError on 'error' status with message fallback."""
        error_resp = _make_response(200, {"status": "error", "message": "bad input"})
        mock_http = _mock_async_client(request=AsyncMock(return_value=error_resp))

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            pytest.raises(RuntimeError, match="bad input"),
        ):
            await client.poll_job("/api/jobs", "job-1", interval=0.01)

    async def test_poll_timeout(self, client: BaseServiceClient) -> None:
        """Verify poll_job raises TimeoutError when wall-clock exceeds timeout."""
        pending_resp = _make_response(200, {"status": "processing"})
        mock_http = _mock_async_client(request=AsyncMock(return_value=pending_resp))

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
            # Infinite tail: w2v_common's client makes a variable number of
            # time.monotonic() reads per poll (the downstream-observe + metering
            # paths added more over 0.26→0.30); a fixed-length list exhausts and
            # raises StopIteration. start=0, every later read=100 (> timeout).
            patch(
                "w2v_common.clients.base.time.monotonic",
                side_effect=itertools.chain([0, 0, 0, 100], itertools.repeat(100)),
            ),
            pytest.raises(TimeoutError, match="timed out"),
        ):
            await client.poll_job("/api/jobs", "job-1", interval=0.01, timeout=1)

    async def test_poll_retries_on_transport_error(self, client: BaseServiceClient) -> None:
        """Verify poll_job catches transport errors and retries with backoff."""
        done_resp = _make_response(200, {"status": "completed"})
        mock_http = _mock_async_client(
            request=AsyncMock(side_effect=[httpx.ConnectError("net fail"), done_resp]),
        )

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
            patch("w2v_common.clients.base.time.monotonic", return_value=0),
        ):
            result = await client.poll_job("/api/jobs", "j1", interval=0.01, timeout=10)

        assert result["status"] == "completed"

    async def test_poll_respects_completed_with_errors(self, client: BaseServiceClient) -> None:
        """Verify poll_job treats 'completed_with_errors' as a terminal success."""
        resp = _make_response(200, {"status": "completed_with_errors", "warnings": ["w1"]})
        mock_http = _mock_async_client(request=AsyncMock(return_value=resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client.poll_job("/api/jobs", "j2", interval=0.01, timeout=10)

        assert result["status"] == "completed_with_errors"
        assert result["warnings"] == ["w1"]

    async def test_poll_respects_partial_status(self, client: BaseServiceClient) -> None:
        """Verify poll_job treats 'partial' as a terminal success."""
        resp = _make_response(200, {"status": "partial", "data": [1]})
        mock_http = _mock_async_client(request=AsyncMock(return_value=resp))

        with patch.object(client, "_get_client", return_value=mock_http):
            result = await client.poll_job("/api/jobs", "j3", interval=0.01, timeout=10)

        assert result["status"] == "partial"
        assert result["data"] == [1]

    async def test_poll_max_attempts_exceeded(self, client: BaseServiceClient) -> None:
        """Verify poll_job raises TimeoutError when max_attempts is exceeded."""
        pending_resp = _make_response(200, {"status": "processing"})
        mock_http = _mock_async_client(request=AsyncMock(return_value=pending_resp))

        with (
            patch.object(client, "_get_client", return_value=mock_http),
            patch("w2v_common.clients.base.asyncio.sleep", new_callable=AsyncMock),
            patch("w2v_common.clients.base.time.monotonic", return_value=0),
            pytest.raises(TimeoutError, match="exceeded 2 poll attempts"),
        ):
            await client.poll_job("/api/jobs", "j4", interval=0.01, timeout=600, max_attempts=2)
