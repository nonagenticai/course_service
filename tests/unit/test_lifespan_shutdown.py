"""Lifespan shutdown must cancel + await in-flight background tasks.

Regression: previously the lifespan teardown closed clients and disposed
the engine without touching ``app.state.background_tasks``. On pod evict /
rolling restart, recovery pipelines could be destroyed mid-write,
producing "Task was destroyed but it is pending" warnings and leaving
courses stuck in non-terminal states — the next pod's recovery loop then
re-picked them up indefinitely.
"""

from __future__ import annotations

import asyncio
import warnings
from types import SimpleNamespace
from typing import Any


class _AppStateStub:
    """Stand-in for FastAPI's app.state with a settable background_tasks set."""

    def __init__(self, tasks: set[asyncio.Task[Any]]) -> None:
        self.background_tasks = tasks


async def _shutdown_cancel(app_state: Any) -> None:
    """Inline copy of the lifespan-shutdown cancel block under test.

    Mirrors src/asgi.py's lifespan teardown — we test the cancel semantics
    in isolation because spinning up the full app lifespan needs a real DB
    engine.
    """
    pending = list(getattr(app_state, "background_tasks", ()))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_shutdown_cancels_long_running_recovery_task() -> None:
    """A long-running pipeline task is cancelled and awaited cleanly."""
    cancelled_handlers_ran = 0

    async def long_recovery() -> None:
        """Simulate a recovery pipeline mid-flight when shutdown fires."""
        nonlocal cancelled_handlers_ran
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled_handlers_ran += 1
            raise

    task = asyncio.create_task(long_recovery())
    # Give the task a tick to enter its sleep before shutdown.
    await asyncio.sleep(0)

    app_state = _AppStateStub({task})
    await _shutdown_cancel(app_state)

    assert task.done()
    assert task.cancelled()
    assert cancelled_handlers_ran == 1


async def test_shutdown_with_no_tasks_is_noop() -> None:
    """Empty background_tasks set must not raise."""
    app_state = _AppStateStub(set())
    await _shutdown_cancel(app_state)


async def test_shutdown_no_attribute_is_noop() -> None:
    """A request scope without app.state.background_tasks at all must not raise."""
    app_state = SimpleNamespace()  # no .background_tasks attribute
    await _shutdown_cancel(app_state)


async def test_shutdown_does_not_emit_task_was_destroyed_warning() -> None:
    """No 'Task was destroyed but it is pending' RuntimeWarning after shutdown.

    That warning is the smoking-gun signal of the bug this fix closes: it
    is emitted only when an event loop is GC'd while a task is still
    pending — i.e. shutdown returned before the task was cancelled+awaited.
    """

    async def long_task() -> None:
        await asyncio.sleep(60)

    task = asyncio.create_task(long_task())
    await asyncio.sleep(0)
    app_state = _AppStateStub({task})

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        await _shutdown_cancel(app_state)
        # The task may still emit its cancellation in a follow-up tick; let it.
        await asyncio.sleep(0)

    bad = [
        w
        for w in captured
        if issubclass(w.category, RuntimeWarning) and "destroyed" in str(w.message)
    ]
    assert not bad, f"unexpected destroyed-task warnings: {[str(w.message) for w in bad]}"
