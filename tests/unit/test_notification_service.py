"""Tests for src.services.notification_service.NotificationService."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.services.notification_service import NotificationService


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.publish = AsyncMock(return_value=2)
    return redis


@pytest.fixture
def notification_service(mock_redis: AsyncMock) -> NotificationService:
    return NotificationService(mock_redis)


class TestPublishProgress:
    async def test_publishes_to_correct_channel_with_correct_payload(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        result = await notification_service.publish_progress(
            "course-001",
            {"progress_pct": 50, "completed": 5},
        )

        # Verify return value is the subscriber count from redis
        assert result == 2

        # Verify the exact channel name follows the pattern course:{id}:progress
        channel, payload = mock_redis.publish.call_args.args
        assert channel == "course:course-001:progress"

        # Verify the payload structure: event field is set to "progress",
        # course_id is embedded, and all progress_data fields are spread in
        assert payload["event"] == "progress"
        assert payload["course_id"] == "course-001"
        assert payload["progress_pct"] == 50
        assert payload["completed"] == 5

        # Verify the payload is JSON-serializable (would raise TypeError if not)
        json.dumps(payload, default=str)

    async def test_publish_progress_returns_subscriber_count(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        mock_redis.publish.return_value = 0
        result = await notification_service.publish_progress("course-002", {})

        # Return value must pass through from redis publish
        assert result == 0

        # Even with empty progress_data, the base fields must exist
        _, payload = mock_redis.publish.call_args.args
        assert payload["event"] == "progress"
        assert payload["course_id"] == "course-002"

    async def test_publish_progress_with_empty_course_id(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        """Empty course_id still produces a channel — the service doesn't validate."""
        result = await notification_service.publish_progress("", {"progress_pct": 100})

        channel, payload = mock_redis.publish.call_args.args
        # Channel becomes "course::progress" with empty id embedded
        assert channel == "course::progress"
        assert payload["course_id"] == ""
        assert result == 2

    async def test_progress_data_fields_are_spread_into_payload(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        """All keys from progress_data appear as top-level payload keys."""
        progress_data: dict[str, Any] = {
            "progress_pct": 75,
            "completed": 3,
            "total": 4,
            "current_phase": "rendering",
        }
        await notification_service.publish_progress("c-99", progress_data)

        _, payload = mock_redis.publish.call_args.args
        for key, value in progress_data.items():
            assert payload[key] == value, f"Missing or wrong value for {key}"


class TestPublishEvent:
    async def test_publishes_event_to_correct_channel_with_details(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        result = await notification_service.publish_event(
            "course-001",
            "lesson_completed",
            {"lesson_id": "lesson-005"},
        )

        assert result == 2

        channel, payload = mock_redis.publish.call_args.args
        # Channel uses :events suffix, distinct from :progress
        assert channel == "course:course-001:events"

        # event field is the event_type argument, NOT a hardcoded "progress"
        assert payload["event"] == "lesson_completed"
        assert payload["course_id"] == "course-001"
        assert payload["details"] == {"lesson_id": "lesson-005"}

        # Payload must be JSON-serializable
        json.dumps(payload, default=str)

    async def test_publishes_event_without_details_omits_key(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        await notification_service.publish_event("course-001", "phase_changed")

        _, payload = mock_redis.publish.call_args.args
        # When details is None, the "details" key must not appear in the payload
        assert "details" not in payload
        assert payload["event"] == "phase_changed"
        assert payload["course_id"] == "course-001"

    async def test_event_type_is_set_correctly_in_payload(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        """The event field must reflect the event_type argument verbatim."""
        for event_type in ("lesson_completed", "phase_changed", "generation_failed"):
            mock_redis.publish.reset_mock()
            await notification_service.publish_event("c-1", event_type)

            _, payload = mock_redis.publish.call_args.args
            assert payload["event"] == event_type

    async def test_event_with_empty_details_dict_omits_key(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        """An empty dict is falsy, so details key should be omitted."""
        await notification_service.publish_event("c-1", "test_event", details={})

        _, payload = mock_redis.publish.call_args.args
        # Empty dict is falsy → details not included in payload
        assert "details" not in payload

    async def test_progress_and_event_use_different_channels_and_payloads(
        self,
        notification_service: NotificationService,
        mock_redis: AsyncMock,
    ) -> None:
        """publish_progress and publish_event produce distinct channel and payload structures."""
        await notification_service.publish_progress("c-1", {"progress_pct": 10})
        progress_channel, progress_payload = mock_redis.publish.call_args.args

        mock_redis.publish.reset_mock()

        await notification_service.publish_event("c-1", "started", {"stage": 1})
        event_channel, event_payload = mock_redis.publish.call_args.args

        # Channels differ
        assert progress_channel == "course:c-1:progress"
        assert event_channel == "course:c-1:events"
        assert progress_channel != event_channel

        # Progress payload has "event": "progress"; event payload has the custom event type
        assert progress_payload["event"] == "progress"
        assert event_payload["event"] == "started"

        # Progress spreads data at top level; event nests under "details"
        assert "progress_pct" in progress_payload
        assert "details" not in progress_payload
        assert "details" in event_payload
        assert event_payload["details"] == {"stage": 1}
