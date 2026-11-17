"""Publish course progress and event notifications via Redis pub/sub."""

from __future__ import annotations

import logging
from typing import Any

from src.infra.redis_client import RedisClient

logger = logging.getLogger(__name__)


class NotificationService:
    """Publish course-scoped events to Redis channels."""

    def __init__(self, redis_client: RedisClient) -> None:
        self._redis = redis_client

    async def publish_progress(self, course_id: str, progress_data: dict[str, Any]) -> int:
        """Publish a progress update to the course progress channel.

        Args:
            course_id: The course identifier.
            progress_data: Dict with progress metrics (progress_pct, completed, etc.).

        Returns:
            Number of subscribers that received the message.
        """
        channel = f"course:{course_id}:progress"
        payload = {"event": "progress", "course_id": course_id, **progress_data}
        result = await self._redis.publish(channel, payload)
        logger.debug("Published progress to %s (%d subscribers)", channel, result)
        return result

    async def publish_event(
        self, course_id: str, event_type: str, details: dict[str, Any] | None = None
    ) -> int:
        """Publish an event notification to the course events channel.

        Args:
            course_id: The course identifier.
            event_type: Type of event (e.g. "lesson_completed", "phase_changed").
            details: Optional extra details for the event.

        Returns:
            Number of subscribers that received the message.
        """
        channel = f"course:{course_id}:events"
        payload: dict[str, Any] = {
            "event": event_type,
            "course_id": course_id,
        }
        if details:
            payload["details"] = details
        result = await self._redis.publish(channel, payload)
        logger.debug("Published event '%s' to %s (%d subscribers)", event_type, channel, result)
        return result
