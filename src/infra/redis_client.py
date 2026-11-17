"""Redis client for course_service — extends w2v_common with publish support."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError
from w2v_common.cache.redis_client import RedisClient as _BaseRedisClient

logger = logging.getLogger(__name__)


class RedisClient(_BaseRedisClient):
    """RedisClient with publish() for course event broadcasting."""

    async def publish(self, channel: str, message: Any) -> int:
        """Publish a message to a Redis channel. Returns subscriber count."""
        try:
            client = self._ensure_connected()
            if isinstance(message, (dict, list)):
                payload = json.dumps(message, default=str)
            else:
                payload = str(message)
            result = await client.publish(channel, payload)
            return int(result)
        except (OSError, RedisError, RuntimeError) as exc:
            logger.warning("Redis publish failed on channel=%s: %s", channel, exc, exc_info=True)
            return 0
