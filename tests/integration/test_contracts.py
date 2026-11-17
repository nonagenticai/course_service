"""Integration contract tests for course_service against live Docker containers.

These tests verify that the course_service's assumptions about downstream
service APIs are correct by sending real HTTP requests to live containers.

Run with:
    uv run pytest tests/integration/test_contracts.py -v --timeout=60
"""

from __future__ import annotations

import httpx
import pytest

HEADERS = {"X-Tenant-Id": "integration-test"}

ORCHESTRATOR_BASE = "http://localhost:6300"
ANALYSIS_BASE = "http://localhost:6303"
SLIDE_BASE = "http://localhost:6312"


# ---------------------------------------------------------------------------
# Course -> Orchestrator contract
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCourseToOrchestratorContract:
    """Verify course_service can call w2v_orchestrator endpoints."""

    async def test_pipeline_create(self) -> None:
        """POST /api/pipeline with lesson config returns a job_id."""
        async with httpx.AsyncClient(base_url=ORCHESTRATOR_BASE, timeout=30) as client:
            resp = await client.post(
                "/api/pipeline",
                json={
                    "config": {"mode": "lesson"},
                    "url": "https://example.com",
                },
                headers=HEADERS,
            )
        assert resp.status_code in {200, 201, 202}, (
            f"Expected 200/201/202, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "job_id" in body, f"Response missing 'job_id': {body}"
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) > 0

    async def test_pipeline_get(self) -> None:
        """GET /api/pipeline/{job_id} returns job details for a previously created pipeline."""
        async with httpx.AsyncClient(base_url=ORCHESTRATOR_BASE, timeout=30) as client:
            # First create a pipeline to get a valid job_id
            create_resp = await client.post(
                "/api/pipeline",
                json={
                    "config": {"mode": "lesson"},
                    "url": "https://example.com",
                },
                headers=HEADERS,
            )
            assert create_resp.status_code in {200, 201, 202}, (
                f"Setup failed: {create_resp.status_code}: {create_resp.text}"
            )
            job_id = create_resp.json()["job_id"]

            resp = await client.get(
                f"/api/pipeline/{job_id}",
                headers=HEADERS,
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "job_id" in body, f"Response missing 'job_id': {body}"
        assert "status" in body, f"Response missing 'status': {body}"


# ---------------------------------------------------------------------------
# Course -> Analysis contract
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCourseToAnalysisContract:
    """Verify course_service can call analysis_service endpoints."""

    async def test_analyze(self) -> None:
        """POST /api/analyze with minimal page data returns a job_id."""
        async with httpx.AsyncClient(base_url=ANALYSIS_BASE, timeout=30) as client:
            resp = await client.post(
                "/api/analyze",
                json={
                    "pages": [
                        {
                            "url": "https://example.com",
                            "html": "<html><body>Course content</body></html>",
                        }
                    ],
                },
                headers=HEADERS,
            )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "job_id" in body, f"Response missing 'job_id': {body}"
        assert isinstance(body["job_id"], str)


# ---------------------------------------------------------------------------
# Course -> Slide contract
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCourseToSlideContract:
    """Verify course_service can call slide_service endpoints."""

    async def test_create_deck(self) -> None:
        """POST /api/decks with a tutorial slide returns 202 with a job_id."""
        async with httpx.AsyncClient(base_url=SLIDE_BASE, timeout=30) as client:
            resp = await client.post(
                "/api/decks",
                json={
                    "title": "Course Module 1",
                    "slides": [
                        {
                            "template_id": "tutorial_step",
                            "content": {
                                "heading": "Lesson 1",
                                "description": "Introduction",
                            },
                        }
                    ],
                },
                headers=HEADERS,
            )
        # Slide service returns 202 for accepted jobs
        assert resp.status_code in {200, 202}, (
            f"Expected 200 or 202, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "job_id" in body, f"Response missing 'job_id': {body}"
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) > 0
