"""Tests for infra/database.py."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from src.infra.database import (
    _ensure_database_exists,
    create_engine_with_retry,
    create_session_factory,
)


class TestEnsureDatabaseExists:
    async def test_database_already_exists(self) -> None:
        """When the database already exists, only SELECT is executed, not CREATE."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1  # DB exists

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

        # Only the SELECT query should have been executed, not CREATE DATABASE
        assert mock_conn.execute.call_count == 1
        select_call_sql = str(mock_conn.execute.call_args_list[0][0][0].text)
        assert "pg_database" in select_call_sql
        assert "SELECT" in select_call_sql.upper()

    async def test_database_created_when_not_exists(self) -> None:
        """When the database does not exist, CREATE DATABASE is executed with the correct name."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None  # DB does not exist

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/newdb",
                max_retries=1,
            )

        assert mock_conn.execute.call_count == 2

        # First call: SELECT to check if DB exists
        select_sql = str(mock_conn.execute.call_args_list[0][0][0].text)
        assert "pg_database" in select_sql

        # Second call: CREATE DATABASE with the correct database name
        create_sql = str(mock_conn.execute.call_args_list[1][0][0].text)
        assert "CREATE DATABASE" in create_sql
        assert "newdb" in create_sql

    async def test_invalid_db_name_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid characters"):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/bad-name!",
                max_retries=1,
            )

    async def test_connection_failure_raises_after_retries(self) -> None:
        """Verify retry count and meaningful error message on connection failure."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = OperationalError("conn fail", {}, Exception("conn fail"))
        mock_engine.dispose = MagicMock()

        mock_sleep = AsyncMock()

        with (
            patch("src.infra.database.create_engine", return_value=mock_engine),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
        ):
            with pytest.raises(ConnectionError, match="Failed to connect") as exc_info:
                await _ensure_database_exists(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=3,
                )

            # Verify all 3 attempts were made
            assert mock_engine.connect.call_count == 3

            # Verify sleep was called between retries (not after last attempt)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(2)  # attempt 1 * 2
            mock_sleep.assert_any_call(4)  # attempt 2 * 2

            # Verify the error message includes host, port, attempts, and last error
            error_msg = str(exc_info.value)
            assert "localhost" in error_msg
            assert "5432" in error_msg
            assert "3 attempts" in error_msg
            assert "conn fail" in error_msg

    async def test_psycopg_url_preserves_credentials(self) -> None:
        """Verify the URL preserves host, port, db, credentials, and psycopg driver."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine) as mock_create:
            await _ensure_database_exists(
                "postgresql+psycopg://myuser:s3cret@dbhost:6543/testdb",
                max_retries=1,
            )

            call_url = mock_create.call_args[0][0]
            # psycopg v3 works for sync engines — driver must be preserved
            assert call_url.startswith("postgresql+psycopg://")
            assert "+asyncpg" not in call_url
            # Credentials preserved
            assert "myuser:s3cret@" in call_url
            # Host and port preserved
            assert "dbhost:6543" in call_url
            # Admin database (default "postgres") used, not the target db
            assert call_url.endswith("/postgres")

    async def test_psycopg_url_preserved(self) -> None:
        """Verify postgresql+psycopg:// URLs are preserved for sync engine (psycopg v3 works sync)."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine) as mock_create:
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

            call_url = mock_create.call_args[0][0]
            assert call_url.startswith("postgresql+psycopg://")
            assert "user:pass@" in call_url

    async def test_ensure_db_uses_nullpool_and_autocommit(self) -> None:
        """Verify create_engine is called with AUTOCOMMIT isolation and NullPool."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine) as mock_create:
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

            _, kwargs = mock_create.call_args
            assert kwargs["isolation_level"] == "AUTOCOMMIT"
            assert kwargs["poolclass"] is NullPool

    async def test_ensure_db_rejects_sql_injection(self) -> None:
        """Database names with special characters (e.g. semicolons) must be rejected."""
        injection_names = [
            "test; DROP TABLE users",
            "db--comment",
            "db' OR '1'='1",
            "db;",
            "name with spaces",
            "db.schema",
        ]
        for bad_name in injection_names:
            with pytest.raises(ValueError, match="invalid characters"):
                await _ensure_database_exists(
                    f"postgresql+psycopg://user:pass@localhost:5432/{bad_name}",
                    max_retries=1,
                )

    async def test_url_conversion_preserves_credentials(self) -> None:
        """Verify that user:password in the URL survive async-to-sync URL conversion."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine) as mock_create:
            await _ensure_database_exists(
                "postgresql+asyncpg://admin_user:P%40ssw0rd%21@pghost:5433/mydb",
                max_retries=1,
            )

            call_url = mock_create.call_args[0][0]
            # Credentials with special characters (URL-encoded) must be preserved
            assert "admin_user:P%40ssw0rd%21@" in call_url
            assert "pghost:5433" in call_url

    async def test_runs_sync_block_in_to_thread_not_event_loop(self) -> None:
        """The blocking sync engine work must run in a worker thread, not the event loop.

        We prove this with a concurrent ``asyncio.sleep(0)``-driven counter task that MUST
        keep ticking while ``_ensure_database_exists_sync`` is "running" — if the sync block
        ran on the event loop, the counter could not advance until it returned.
        """
        import time

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            for _ in range(50):
                ticks += 1
                await asyncio.sleep(0.001)

        # The fake sync helper sleeps for 200ms with time.sleep — if this is on the event
        # loop, the ticker task will be blocked.
        def fake_sync(_url: str, _name: str) -> None:
            time.sleep(0.2)

        ticker_task = asyncio.create_task(ticker())

        with patch("src.infra.database._ensure_database_exists_sync", side_effect=fake_sync):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

        await ticker_task
        # If the sync block had run on the event loop, ticks would have stalled at <5.
        # to_thread offload means the loop was free to advance the ticker concurrently.
        assert ticks == 50, f"event loop appears blocked during sync DB bootstrap (ticks={ticks})"

    async def test_to_thread_is_actually_used(self) -> None:
        """Spy on asyncio.to_thread to confirm the sync block is offloaded."""
        with (
            patch("src.infra.database._ensure_database_exists_sync") as mock_sync,
            patch(
                "src.infra.database.asyncio.to_thread",
                wraps=asyncio.to_thread,
            ) as spy,
        ):
            mock_sync.return_value = None
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )
            assert spy.called, "asyncio.to_thread must be used to offload the sync DB block"
            args, _ = spy.call_args
            assert args[0] is mock_sync

    async def test_engine_disposed_on_success(self) -> None:
        """Verify the sync engine is disposed after successful operation."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

        mock_engine.dispose.assert_called_once()


class TestCreateEngineWithRetry:
    async def test_success_first_attempt(self) -> None:
        """Verify create_async_engine is called with the expected URL and pool params."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock()
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        test_url = "postgresql+psycopg://user:pass@localhost:5432/testdb"

        with patch(
            "src.infra.database.create_async_engine", return_value=mock_engine
        ) as mock_create:
            engine = await create_engine_with_retry(
                test_url,
                pool_size=5,
                max_overflow=10,
                max_retries=1,
            )

            assert engine is mock_engine

            # Verify create_async_engine was called exactly once (success on first try)
            mock_create.assert_called_once()

            # Verify the URL passed to create_async_engine
            actual_url = mock_create.call_args[0][0]
            assert actual_url == test_url

            # Verify pool parameters
            _, kwargs = mock_create.call_args
            assert kwargs["pool_size"] == 5
            assert kwargs["max_overflow"] == 10
            assert kwargs["poolclass"] is AsyncAdaptedQueuePool
            assert kwargs["pool_pre_ping"] is True

    async def test_failure_raises_connection_error(self) -> None:
        mock_sleep = AsyncMock()

        with (
            patch(
                "src.infra.database.create_async_engine",
                side_effect=OSError("Connection refused"),
            ),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
        ):
            with pytest.raises(ConnectionError, match="Failed to connect") as exc_info:
                await create_engine_with_retry(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=2,
                    retry_delay=0.01,
                )

            error_msg = str(exc_info.value)
            assert "localhost" in error_msg
            assert "5432" in error_msg
            assert "2 attempts" in error_msg

    async def test_db_not_exist_triggers_ensure(self) -> None:
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock()
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OperationalError(
                    'database "testdb" does not exist',
                    {},
                    Exception("db missing"),
                )
            return mock_engine

        mock_ensure = AsyncMock()

        with (
            patch("src.infra.database.create_async_engine", side_effect=side_effect),
            patch(
                "src.infra.database._ensure_database_exists",
                mock_ensure,
            ),
            patch("src.infra.database.asyncio.sleep", new_callable=AsyncMock),
        ):
            engine = await create_engine_with_retry(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=3,
                retry_delay=0.01,
            )
            assert engine is mock_engine

            # Verify _ensure_database_exists was called with the right URL
            mock_ensure.assert_called_once_with(
                "postgresql+psycopg://user:pass@localhost:5432/testdb"
            )

    async def test_create_engine_passes_pool_settings(self) -> None:
        """Verify all pool settings are forwarded to create_async_engine."""
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_ctx.__aexit__ = AsyncMock()
        mock_engine.begin = MagicMock(return_value=mock_ctx)

        with patch(
            "src.infra.database.create_async_engine", return_value=mock_engine
        ) as mock_create:
            await create_engine_with_retry(
                "postgresql+psycopg://u:p@h:1/db",
                pool_size=3,
                max_overflow=7,
                pool_timeout=15,
                pool_recycle=1800,
                echo=True,
                max_retries=1,
            )

            _, kwargs = mock_create.call_args
            assert kwargs["pool_size"] == 3
            assert kwargs["max_overflow"] == 7
            assert kwargs["pool_timeout"] == 15
            assert kwargs["pool_recycle"] == 1800
            assert kwargs["echo"] is True
            assert kwargs["poolclass"] is AsyncAdaptedQueuePool
            assert kwargs["pool_pre_ping"] is True

    async def test_retry_with_exponential_backoff(self) -> None:
        """Verify that retries use exponential backoff with doubling delay."""
        mock_sleep = AsyncMock()

        with (
            patch(
                "src.infra.database.create_async_engine",
                side_effect=OSError("fail"),
            ),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
            pytest.raises(ConnectionError),
        ):
            await create_engine_with_retry(
                "postgresql+psycopg://u:p@h:1/db",
                max_retries=4,
                retry_delay=1.0,
            )

        # Sleeps between attempts: 1.0, 2.0, 4.0 (no sleep after last attempt)
        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list[0] == call(1.0)
        assert mock_sleep.call_args_list[1] == call(2.0)
        assert mock_sleep.call_args_list[2] == call(4.0)


class TestCreateSessionFactory:
    def test_returns_sessionmaker(self) -> None:
        """Verify the returned object is an async_sessionmaker bound to the engine."""
        mock_engine = MagicMock()
        factory = create_session_factory(mock_engine)

        assert isinstance(factory, async_sessionmaker)

    def test_session_factory_configuration(self) -> None:
        """Verify session factory is configured with correct parameters."""
        mock_engine = MagicMock()
        factory = create_session_factory(mock_engine)

        assert isinstance(factory, async_sessionmaker)
        assert factory.class_ is AsyncSession
        # Verify key session settings via the kw dict
        assert factory.kw.get("expire_on_commit") is False
        assert factory.kw.get("autocommit") is False
        assert factory.kw.get("autoflush") is False


class TestEnsureDatabaseExistsEdgeCases:
    """Tests for missed lines in _ensure_database_exists."""

    async def test_plain_postgresql_url_passthrough(self) -> None:
        """Line 50: URL without +psycopg is used as-is."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine) as mock_create:
            await _ensure_database_exists(
                "postgresql://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

            call_url = mock_create.call_args[0][0]
            assert call_url.startswith("postgresql://")
            assert "+psycopg" not in call_url
            assert "+asyncpg" not in call_url

    async def test_create_database_race_condition_already_exists(self) -> None:
        """Lines 76-79: ProgrammingError 'already exists' during CREATE is handled."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None  # DB appears not to exist

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result  # SELECT returns None
            # CREATE DATABASE raises "already exists"
            raise ProgrammingError(
                'database "testdb" already exists',
                {},
                Exception('database "testdb" already exists'),
            )

        mock_conn.execute = MagicMock(side_effect=execute_side_effect)
        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            # Should not raise -- the "already exists" error is handled gracefully
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )

    async def test_create_database_programming_error_not_already_exists_reraises(self) -> None:
        """Lines 76-79: ProgrammingError that is NOT 'already exists' is re-raised."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        call_count = 0

        def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result
            raise ProgrammingError("permission denied", {}, Exception("permission denied"))

        mock_conn.execute = MagicMock(side_effect=execute_side_effect)
        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            with pytest.raises(ProgrammingError, match="permission denied"):
                await _ensure_database_exists(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=1,
                )

    async def test_unexpected_exception_is_reraised(self) -> None:
        """Lines 84-86: Unexpected exceptions are logged and re-raised."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = RuntimeError("unexpected failure")
        mock_engine.dispose = MagicMock()

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            with pytest.raises(RuntimeError, match="unexpected failure"):
                await _ensure_database_exists(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=1,
                )

    async def test_engine_dispose_failure_in_finally(self) -> None:
        """Lines 91-92: engine.dispose() failure in finally block is handled."""
        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1

        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_result

        mock_engine.connect.return_value = mock_conn
        mock_engine.dispose = MagicMock(side_effect=OSError("dispose failed"))

        with patch("src.infra.database.create_engine", return_value=mock_engine):
            # Should not raise -- dispose failure is logged but swallowed
            await _ensure_database_exists(
                "postgresql+psycopg://user:pass@localhost:5432/testdb",
                max_retries=1,
            )


class TestCreateEngineWithRetryEdgeCases:
    """Tests for missed lines in create_engine_with_retry."""

    async def test_ensure_db_fails_records_last_error(self) -> None:
        """Lines 142-148: _ensure_database_exists failure is captured as last_error."""
        mock_sleep = AsyncMock()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise OperationalError(
                'database "testdb" does not exist',
                {},
                Exception("db missing"),
            )

        mock_ensure = AsyncMock(side_effect=ConnectionError("cannot create db"))

        with (
            patch("src.infra.database.create_async_engine", side_effect=side_effect),
            patch("src.infra.database._ensure_database_exists", mock_ensure),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
        ):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await create_engine_with_retry(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=2,
                    retry_delay=0.01,
                )

    async def test_generic_exception_retries_and_fails(self) -> None:
        """Lines 156-160: Generic Exception triggers retry with exponential backoff."""
        mock_sleep = AsyncMock()

        with (
            patch(
                "src.infra.database.create_async_engine",
                side_effect=ValueError("bad config"),
            ),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
        ):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await create_engine_with_retry(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=3,
                    retry_delay=1.0,
                )

        # Should have slept between attempts: 1.0, 2.0 (not after last)
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list[0] == call(1.0)
        assert mock_sleep.call_args_list[1] == call(2.0)

    async def test_operational_error_non_missing_db_retries(self) -> None:
        """Line 145: OperationalError without 'does not exist' sets last_error and retries."""
        mock_sleep = AsyncMock()

        with (
            patch(
                "src.infra.database.create_async_engine",
                side_effect=OperationalError(
                    "connection timeout", {}, Exception("connection timeout")
                ),
            ),
            patch("src.infra.database.asyncio.sleep", mock_sleep),
        ):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await create_engine_with_retry(
                    "postgresql+psycopg://user:pass@localhost:5432/testdb",
                    max_retries=2,
                    retry_delay=0.01,
                )
