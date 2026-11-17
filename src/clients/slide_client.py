"""HTTP client for the slide generation service."""

from __future__ import annotations

import logging
from typing import Any

from src.clients.base import BaseServiceClient

logger = logging.getLogger(__name__)


class SlideClient(BaseServiceClient):
    """Client for creating and retrieving slide decks."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        license_token: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            service_name="slide",
            timeout=timeout,
            license_token=license_token,
        )

    async def create_slide_deck(
        self,
        module_title: str,
        lessons: list[dict[str, Any]],
        config: dict[str, Any] | None = None,
    ) -> str:
        """Submit a slide deck generation job and return the job ID.

        Translates course-domain inputs (module_title, lessons) into the
        slide_service DeckCreateRequest format (title, slides).

        Args:
            module_title: Title of the module for the slide deck.
            lessons: List of lesson dicts with title, topic, description.
            config: Optional slide generation configuration.

        Returns:
            Job ID string for polling.
        """
        slides = [
            {
                "template_name": "tutorial_step",
                "content": {
                    "title": lesson.get("title", ""),
                    "topic": lesson.get("topic", ""),
                    "description": lesson.get("description", ""),
                },
            }
            for lesson in lessons
        ]
        payload: dict[str, Any] = {
            "title": module_title,
            "slides": slides,
        }
        if config:
            payload["config"] = config

        result = await self._request("POST", "/api/decks", json=payload)
        job_id = result.get("job_id", "")
        logger.info("slide: submitted job_id=%s for module=%s", job_id, module_title)
        return job_id

    async def get_slide_deck(self, job_id: str) -> dict[str, Any]:
        """Get slide deck generation status and result by job ID."""
        return await self._request("GET", f"/api/decks/{job_id}")

    async def wait_for_slide_deck(
        self,
        job_id: str,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Poll a slide deck job until completion."""
        return await self.poll_job(
            endpoint="/api/decks",
            job_id=job_id,
            interval=3.0,
            timeout=timeout,
        )
