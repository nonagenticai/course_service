"""HTTP client for the analysis_service API."""

from __future__ import annotations

import logging
from typing import Any

from src.clients.base import BaseServiceClient

logger = logging.getLogger(__name__)


class AnalysisClient(BaseServiceClient):
    """Client for submitting analysis jobs and retrieving results."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        license_token: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            service_name="analysis",
            timeout=timeout,
            license_token=license_token,
        )

    async def submit_analysis(
        self,
        pages: list[dict[str, Any]],
        user_context: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        """Submit an analysis job and return the job ID.

        Args:
            pages: List of crawled page dicts (required by AnalyzeRequest).
            user_context: Optional user context for the analysis.
            config: Optional analysis configuration overrides.
        """
        payload: dict[str, Any] = {"pages": pages}
        if user_context:
            payload["user_context"] = user_context
        if config:
            payload["config"] = config

        result = await self._request("POST", "/api/analyze", json=payload)
        job_id = result.get("job_id", "")
        logger.info("analysis: submitted job_id=%s for %d pages", job_id, len(pages))
        return job_id

    async def get_analysis(self, job_id: str) -> dict[str, Any]:
        """Get analysis result by job ID."""
        return await self._request("GET", f"/api/analyze/{job_id}")

    async def wait_for_analysis(
        self,
        job_id: str,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Poll an analysis job until completion."""
        return await self.poll_job(
            endpoint="/api/analyze",
            job_id=job_id,
            interval=3.0,
            timeout=timeout,
        )
