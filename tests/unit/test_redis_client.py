"""Tests for infra/redis_client.py — course_service RedisClient with publish()."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from src.infra.redis_client import RedisClient


@pytest.fixture
def redis_client() -> RedisClient:
    return RedisClient(
        "redis://localhost:6379/0",
        max_connections=5,
        socket_timeout=3,
        socket_connect_timeout=3,
        decode_responses=True,
    )


class TestInit:
    def test_stores_config(self) -> None:
        client = RedisClient("redis://localhost:6379/1", max_connections=20)
        assert client._redis_url == "redis://localhost:6379/1"
        assert client._max_connections == 20
        assert client._pool is None
        assert client._client is None


class TestSerialize:
    def test_dict(self, redis_client: RedisClient) -> None:
        assert json.loads(redis_client._serialize({"key": "val"})) == {"key": "val"}

    def test_pydantic_model(self, redis_client: RedisClient) -> None:
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"a": 1}
        result = redis_client._serialize(mock_model)
        assert json.loads(result) == {"a": 1}
        mock_model.model_dump.assert_called_once_with(mode="json")

    def test_list(self, redis_client: RedisClient) -> None:
        assert json.loads(redis_client._serialize([1, 2, 3])) == [1, 2, 3]


class TestDeserialize:
    def test_none_returns_none(self, redis_client: RedisClient) -> None:
        assert redis_client._deserialize(None) is None

    def test_valid_json(self, redis_client: RedisClient) -> None:
        assert redis_client._deserialize('{"k": "v"}') == {"k": "v"}

    def test_invalid_json_returns_none(self, redis_client: RedisClient) -> None:
        assert redis_client._deserialize("not json{{{") is None


class TestEnsureConnected:
    def test_raises_when_not_connected(self, redis_client: RedisClient) -> None:
        with pytest.raises(RuntimeError, match="not connected"):
            redis_client._ensure_connected()

    def test_returns_client_when_connected(self, redis_client: RedisClient) -> None:
        mock_redis = MagicMock()
        redis_client._client = mock_redis
        assert redis_client._ensure_connected() is mock_redis


class TestConnect:
    async def test_connect_creates_pool_and_client(self, redis_client: RedisClient) -> None:
        mock_pool = MagicMock()
        mock_redis_instance = MagicMock()
        mock_ping = AsyncMock(return_value=True)
        mock_redis_instance.ping = mock_ping

        with (
            patch(
                "w2v_common.cache.redis_client.ConnectionPool.from_url",
                return_value=mock_pool,
            ) as mock_from_url,
            patch(
                "w2v_common.cache.redis_client.redis.Redis",
                return_value=mock_redis_instance,
            ) as mock_redis_cls,
        ):
            await redis_client.connect()

        # Verify pool was created with correct config from constructor
        mock_from_url.assert_called_once_with(
            "redis://localhost:6379/0",
            max_connections=5,
            socket_timeout=3,
            socket_connect_timeout=3,
            decode_responses=True,
            health_check_interval=0,
        )
        # Verify Redis client was created with the pool
        mock_redis_cls.assert_called_once_with(connection_pool=mock_pool)
        # Verify state is stored
        assert redis_client._pool is mock_pool
        assert redis_client._client is mock_redis_instance

    async def test_connect_skips_when_pool_exists(self, redis_client: RedisClient) -> None:
        existing_pool = MagicMock()
        existing_client = MagicMock()
        redis_client._pool = existing_pool
        redis_client._client = existing_client

        await redis_client.connect()  # Should not create new pool

        # Pool and client remain unchanged
        assert redis_client._pool is existing_pool
        assert redis_client._client is existing_client


class TestDisconnect:
    async def test_disconnect_closes_and_clears(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_pool = AsyncMock()
        redis_client._client = mock_client
        redis_client._pool = mock_pool

        await redis_client.disconnect()

        mock_client.aclose.assert_called_once()
        mock_pool.aclose.assert_called_once()
        assert redis_client._client is None
        assert redis_client._pool is None

    async def test_disconnect_when_not_connected(self, redis_client: RedisClient) -> None:
        await redis_client.disconnect()  # No crash


class TestGet:
    async def test_get_returns_deserialized_value(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.get.return_value = '{"data": 42}'
        redis_client._client = mock_client

        result = await redis_client.get("my-key")

        # Value is deserialized from JSON string to dict
        assert result == {"data": 42}
        assert isinstance(result, dict)
        mock_client.get.assert_called_once_with("my-key")

    async def test_get_returns_none_for_missing_key(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.get.return_value = None
        redis_client._client = mock_client

        result = await redis_client.get("missing")
        assert result is None

    async def test_get_returns_none_on_error(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.get.side_effect = RedisError("fail")
        redis_client._client = mock_client

        result = await redis_client.get("error-key")
        assert result is None

    async def test_get_deserializes_json(self, redis_client: RedisClient) -> None:
        """Verify returned value is actually deserialized from JSON, not raw string."""
        mock_client = AsyncMock()
        raw_json = json.dumps({"nested": {"list": [1, 2, 3]}, "flag": True})
        mock_client.get.return_value = raw_json
        redis_client._client = mock_client

        result = await redis_client.get("complex-key")

        # Must be a real dict, not a string
        assert isinstance(result, dict)
        assert result["nested"]["list"] == [1, 2, 3]
        assert result["flag"] is True


class TestSet:
    async def test_set_without_ttl(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.set.return_value = True
        redis_client._client = mock_client

        result = await redis_client.set("k", {"val": 1})
        assert result is True

        # Verify exact args: key and JSON-serialized value
        mock_client.set.assert_called_once_with("k", json.dumps({"val": 1}))
        # Confirm setex was NOT called (no TTL)
        mock_client.setex.assert_not_called()

    async def test_set_with_ttl(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.setex.return_value = True
        redis_client._client = mock_client

        result = await redis_client.set("k", "val", ttl=300)
        assert result is True

        # Verify setex was called with key, ttl, and JSON-serialized value
        mock_client.setex.assert_called_once_with("k", 300, json.dumps("val"))
        # Confirm set was NOT called
        mock_client.set.assert_not_called()

    async def test_set_returns_false_on_error(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.set.side_effect = RedisError("fail")
        redis_client._client = mock_client

        result = await redis_client.set("k", "v")
        assert result is False

    async def test_set_serializes_value_to_json(self, redis_client: RedisClient) -> None:
        """Verify that the value passed to redis.set() is JSON-encoded."""
        mock_client = AsyncMock()
        mock_client.set.return_value = True
        redis_client._client = mock_client

        original = {"items": [1, 2], "nested": {"key": "value"}}
        await redis_client.set("json-key", original)

        # The second argument to set() must be a JSON string
        actual_serialized = mock_client.set.call_args.args[1]
        assert isinstance(actual_serialized, str)
        assert json.loads(actual_serialized) == original


class TestDelete:
    async def test_delete_success(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.delete.return_value = 1
        redis_client._client = mock_client

        result = await redis_client.delete("k")
        assert result is True

    async def test_delete_returns_false_on_error(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.delete.side_effect = RedisError("fail")
        redis_client._client = mock_client

        result = await redis_client.delete("k")
        assert result is False


class TestPing:
    async def test_ping_success_awaitable(self, redis_client: RedisClient) -> None:
        mock_client = MagicMock()
        mock_ping = AsyncMock(return_value=True)
        mock_client.ping = mock_ping
        redis_client._client = mock_client

        result = await redis_client.ping()
        assert result is True

    async def test_ping_returns_false_on_error(self, redis_client: RedisClient) -> None:
        # ping() is not connected -> returns False
        result = await redis_client.ping()
        assert result is False


class TestPublish:
    async def test_publish_success(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.publish.return_value = 3
        redis_client._client = mock_client

        result = await redis_client.publish("my-channel", {"event": "test"})

        assert result == 3
        # Verify exact args: channel name AND JSON-serialized message
        mock_client.publish.assert_called_once_with(
            "my-channel",
            json.dumps({"event": "test"}),
        )

    async def test_publish_serializes_dict_message_to_json(self, redis_client: RedisClient) -> None:
        """Verify that a dict message is JSON-encoded before publish."""
        mock_client = AsyncMock()
        mock_client.publish.return_value = 1
        redis_client._client = mock_client

        message = {"event": "progress", "data": [1, 2, 3]}
        await redis_client.publish("ch", message)

        # The second argument must be a JSON string, not a raw dict
        actual_payload = mock_client.publish.call_args.args[1]
        assert isinstance(actual_payload, str)
        assert json.loads(actual_payload) == message

    async def test_publish_serializes_list_message_to_json(self, redis_client: RedisClient) -> None:
        """Verify that a list message is JSON-encoded before publish."""
        mock_client = AsyncMock()
        mock_client.publish.return_value = 1
        redis_client._client = mock_client

        message = [{"id": 1}, {"id": 2}]
        await redis_client.publish("ch", message)

        actual_payload = mock_client.publish.call_args.args[1]
        assert isinstance(actual_payload, str)
        assert json.loads(actual_payload) == message

    async def test_publish_converts_non_dict_to_string(self, redis_client: RedisClient) -> None:
        """Non-dict/list messages are converted via str(), not json.dumps."""
        mock_client = AsyncMock()
        mock_client.publish.return_value = 1
        redis_client._client = mock_client

        await redis_client.publish("ch", 42)

        actual_payload = mock_client.publish.call_args.args[1]
        assert actual_payload == "42"

    async def test_publish_uses_default_str_for_non_serializable(
        self, redis_client: RedisClient
    ) -> None:
        """json.dumps with default=str handles non-serializable objects in dicts."""
        mock_client = AsyncMock()
        mock_client.publish.return_value = 1
        redis_client._client = mock_client

        from datetime import datetime

        dt = datetime(2026, 1, 1, 12, 0, 0)
        message = {"timestamp": dt, "value": 42}
        await redis_client.publish("ch", message)

        actual_payload = mock_client.publish.call_args.args[1]
        parsed = json.loads(actual_payload)
        # datetime should be serialized to its str() representation
        assert parsed["timestamp"] == str(dt)
        assert parsed["value"] == 42

    async def test_publish_returns_zero_on_error(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.publish.side_effect = ConnectionError("fail")
        redis_client._client = mock_client

        result = await redis_client.publish("channel", "msg")
        assert result == 0

    async def test_publish_returns_zero_on_redis_error(self, redis_client: RedisClient) -> None:
        mock_client = AsyncMock()
        mock_client.publish.side_effect = RedisError("connection lost")
        redis_client._client = mock_client

        result = await redis_client.publish("channel", {"data": 1})
        assert result == 0

    async def test_publish_returns_int(self, redis_client: RedisClient) -> None:
        """Verify publish() returns an int (cast from redis response)."""
        mock_client = AsyncMock()
        mock_client.publish.return_value = 5
        redis_client._client = mock_client

        result = await redis_client.publish("ch", "msg")
        assert isinstance(result, int)
        assert result == 5
