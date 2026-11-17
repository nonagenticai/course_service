"""Tests for src.clients — OrchestratorClient, AnalysisClient, SlideClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.clients.analysis_client import AnalysisClient
from src.clients.orchestrator_client import _POLL_SLICE_SECONDS, OrchestratorClient
from src.clients.slide_client import SlideClient


class TestOrchestratorClient:
    """Tests for OrchestratorClient.create_pipeline payload construction and return values."""

    @pytest.fixture
    def orch_client(self) -> OrchestratorClient:
        return OrchestratorClient(base_url="http://orchestrator:6300")

    async def test_create_pipeline_builds_correct_payload(
        self, orch_client: OrchestratorClient
    ) -> None:
        """Verify payload construction AND that the response dict is returned."""
        with patch.object(orch_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "pipe-001"}

            result = await orch_client.create_pipeline(
                mode="lesson",
                prior_results={"analyze": {"pages": []}},
                user_context={"tone": "formal"},
                callback_url="http://localhost:6314/api/courses/c1/callback",
                config_overrides={"target_duration_minutes": 2.0},
            )

        # Verify return value
        assert result == {"job_id": "pipe-001"}
        assert result["job_id"] == "pipe-001"

        # Verify HTTP method and endpoint
        mock_req.assert_awaited_once()
        call_args = mock_req.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1] == "/api/pipeline"

        # Verify payload structure
        payload = call_args.kwargs["json"]
        assert payload["config"]["mode"] == "lesson"
        assert payload["config"]["target_duration_minutes"] == 2.0
        assert payload["prior_results"]["analyze"]["pages"] == []
        assert payload["user_context"]["tone"] == "formal"
        assert payload["callback_url"] == "http://localhost:6314/api/courses/c1/callback"

    async def test_create_pipeline_minimal_payload(self, orch_client: OrchestratorClient) -> None:
        """Verify minimal call omits optional fields and returns the result."""
        with patch.object(orch_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "pipe-002"}

            result = await orch_client.create_pipeline()

        assert result["job_id"] == "pipe-002"
        payload = mock_req.call_args.kwargs["json"]
        assert payload["config"]["mode"] == "lesson"
        assert "prior_results" not in payload
        assert "user_context" not in payload
        assert "callback_url" not in payload
        assert "url" not in payload

    async def test_create_pipeline_returns_job_id(self, orch_client: OrchestratorClient) -> None:
        """Verify create_pipeline returns the full response dict including job_id."""
        with patch.object(orch_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "pipe-003", "status": "queued"}

            result = await orch_client.create_pipeline(url="https://example.com")

        assert result["job_id"] == "pipe-003"
        assert result["status"] == "queued"
        # Verify url was included in payload
        payload = mock_req.call_args.kwargs["json"]
        assert payload["url"] == "https://example.com"

    async def test_wait_for_pipeline_delegates_to_poll_job(
        self, orch_client: OrchestratorClient
    ) -> None:
        """Verify wait_for_pipeline calls poll_job with correct endpoint and args.

        The `timeout` handed to `poll_job` is a SLICE of the caller's budget, never
        the budget itself — `poll_job` clamps it and raises above 600s. See
        `test_poll_timeouts_within_client_ceiling.py`.
        """
        with patch.object(orch_client, "poll_job", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "completed", "video_url": "https://x"}

            result = await orch_client.wait_for_pipeline("pipe-001", timeout=600.0)

        mock_poll.assert_awaited_once_with(
            endpoint="/api/pipeline",
            job_id="pipe-001",
            interval=5.0,
            timeout=_POLL_SLICE_SECONDS,
        )
        assert result["status"] == "completed"
        assert result["video_url"] == "https://x"

    async def test_get_pipeline_status(self, orch_client: OrchestratorClient) -> None:
        """Verify get_pipeline_status uses GET with the correct endpoint."""
        with patch.object(orch_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "processing", "progress": 50}

            result = await orch_client.get_pipeline_status("pipe-001")

        mock_req.assert_awaited_once_with("GET", "/api/pipeline/pipe-001")
        assert result["status"] == "processing"
        assert result["progress"] == 50


class TestAnalysisClient:
    """Tests for AnalysisClient.submit_analysis."""

    @pytest.fixture
    def analysis_client(self) -> AnalysisClient:
        return AnalysisClient(base_url="http://analysis:6303")

    async def test_submit_analysis_returns_job_id(self, analysis_client: AnalysisClient) -> None:
        """Verify submit_analysis posts to correct endpoint and extracts job_id string."""
        pages = [{"url": "https://example.com", "html": "<html>Hi</html>"}]

        with patch.object(analysis_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "analysis-001"}

            job_id = await analysis_client.submit_analysis(pages, config={"depth": 3})

        # Verify return is the extracted job_id string, not the whole dict
        assert job_id == "analysis-001"
        assert isinstance(job_id, str)

        # Verify HTTP method and endpoint
        mock_req.assert_awaited_once()
        call_args = mock_req.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1] == "/api/analyze"

        # Verify payload
        payload = call_args.kwargs["json"]
        assert payload["pages"] == pages
        assert payload["config"]["depth"] == 3

    async def test_submit_analysis_without_config(self, analysis_client: AnalysisClient) -> None:
        """Verify config is omitted from payload when not provided."""
        pages = [{"url": "https://example.com", "html": "<html></html>"}]

        with patch.object(analysis_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "analysis-002"}

            job_id = await analysis_client.submit_analysis(pages)

        assert job_id == "analysis-002"
        payload = mock_req.call_args.kwargs["json"]
        assert payload["pages"] == pages
        assert "config" not in payload

    async def test_submit_analysis_missing_job_id_in_response(
        self, analysis_client: AnalysisClient
    ) -> None:
        """Verify submit_analysis returns empty string when response lacks job_id."""
        pages = [{"url": "https://example.com"}]

        with patch.object(analysis_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "queued"}  # no job_id key

            job_id = await analysis_client.submit_analysis(pages)

        # The implementation uses .get("job_id", "") so we get empty string
        assert job_id == ""

    async def test_wait_for_analysis_delegates_to_poll_job(
        self, analysis_client: AnalysisClient
    ) -> None:
        """Verify wait_for_analysis calls poll_job with correct endpoint and args."""
        with patch.object(analysis_client, "poll_job", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"status": "completed", "result": {"pages": []}}

            result = await analysis_client.wait_for_analysis("a-001", timeout=120.0)

        mock_poll.assert_awaited_once_with(
            endpoint="/api/analyze",
            job_id="a-001",
            interval=3.0,
            timeout=120.0,
        )
        assert result["status"] == "completed"

    async def test_get_analysis(self, analysis_client: AnalysisClient) -> None:
        """Verify get_analysis uses GET with the correct endpoint."""
        with patch.object(analysis_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "completed", "data": {"pages": [1, 2]}}

            result = await analysis_client.get_analysis("a-001")

        mock_req.assert_awaited_once_with("GET", "/api/analyze/a-001")
        assert result["data"]["pages"] == [1, 2]


class TestSlideClient:
    """Tests for SlideClient.create_slide_deck."""

    @pytest.fixture
    def slide_client(self) -> SlideClient:
        return SlideClient(base_url="http://slides:6312")

    async def test_create_slide_deck_builds_correct_payload(
        self, slide_client: SlideClient
    ) -> None:
        """Verify payload construction and job_id extraction."""
        lessons = [
            {"title": "Lesson 1", "topic": "Intro", "description": "Intro desc"},
            {"title": "Lesson 2", "topic": "Deep dive", "description": "Details"},
        ]

        with patch.object(slide_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "slide-001"}

            job_id = await slide_client.create_slide_deck(
                module_title="Module A",
                lessons=lessons,
                config={"theme": "dark"},
            )

        # Verify extracted job_id string
        assert job_id == "slide-001"
        assert isinstance(job_id, str)

        # Verify HTTP method and endpoint
        mock_req.assert_awaited_once()
        call_args = mock_req.call_args
        assert call_args.args[0] == "POST"
        assert call_args.args[1] == "/api/decks"

        # Verify payload
        payload = call_args.kwargs["json"]
        assert payload["title"] == "Module A"
        assert len(payload["slides"]) == 2
        assert payload["slides"][0]["content"]["title"] == "Lesson 1"
        assert payload["config"]["theme"] == "dark"

    async def test_create_slide_deck_without_config(self, slide_client: SlideClient) -> None:
        """Verify config is omitted from payload when not provided."""
        with patch.object(slide_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"job_id": "slide-002"}

            job_id = await slide_client.create_slide_deck(
                module_title="M1", lessons=[{"title": "L1"}]
            )

        assert job_id == "slide-002"
        payload = mock_req.call_args.kwargs["json"]
        assert payload["title"] == "M1"
        assert "config" not in payload

    async def test_create_slide_deck_missing_job_id_in_response(
        self, slide_client: SlideClient
    ) -> None:
        """Verify create_slide_deck returns empty string when response lacks job_id."""
        with patch.object(slide_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "accepted"}  # no job_id

            job_id = await slide_client.create_slide_deck(
                module_title="M1", lessons=[{"title": "L1"}]
            )

        assert job_id == ""

    async def test_wait_for_slide_deck_delegates_to_poll_job(
        self, slide_client: SlideClient
    ) -> None:
        """Verify wait_for_slide_deck calls poll_job with correct endpoint and args."""
        with patch.object(slide_client, "poll_job", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {
                "status": "completed",
                "pptx_path": "/slides/output.pptx",
            }

            result = await slide_client.wait_for_slide_deck("s-001", timeout=120.0)

        mock_poll.assert_awaited_once_with(
            endpoint="/api/decks",
            job_id="s-001",
            interval=3.0,
            timeout=120.0,
        )
        assert result["status"] == "completed"
        assert result["pptx_path"] == "/slides/output.pptx"

    async def test_get_slide_deck(self, slide_client: SlideClient) -> None:
        """Verify get_slide_deck uses GET with the correct endpoint."""
        with patch.object(slide_client, "_request", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"status": "processing", "progress": 75}

            result = await slide_client.get_slide_deck("s-001")

        mock_req.assert_awaited_once_with("GET", "/api/decks/s-001")
        assert result["progress"] == 75
