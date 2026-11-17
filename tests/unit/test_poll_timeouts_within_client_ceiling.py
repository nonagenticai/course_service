"""Every deadline this service hands `BaseServiceClient` must be one the client ACCEPTS.

`BaseServiceClient.poll_job` opens with `timeout = _clamp_timeout(timeout)`, and
`_clamp_timeout` RAISES `ValueError` outside its bounds (`_MIN_TIMEOUT = 1.0`,
`_MAX_TIMEOUT = 600.0`, the ceiling overridable by `CLIENT_MAX_TIMEOUT_S` — which is
set in no repo and in no gitops manifest, so 600 is the live value). It does NOT
silently cap. A deadline above the ceiling therefore does not merely fail to help:
the poll blows up before the first GET, on every call.

⚠️ NOTHING ELSE IN THIS SUITE CATCHES THAT. `OrchestratorClient.wait_for_pipeline`
shipped `timeout: float = 1800.0` passed straight through to `poll_job`, and
`LessonDispatcher` handed it a 1800.0 `lesson_timeout`, so every lesson dispatch
raised `ValueError` before its first HTTP call. Every unit test over that path
passed, because they all mock `poll_job` or mock the whole client — the real
`_clamp_timeout` never ran in any of them. That is precisely the hole these tests
close: each one drives the REAL library clamp.

Three properties make them hard to fool, and all three are deliberate:

1. They read the bounds FROM THE LIBRARY (`_clamp_timeout` / `_MAX_TIMEOUT`), never a
   hardcoded 600. A hardcoded variant asserts a number that can quietly become wrong
   in the other direction if w2v_common moves the ceiling; this tracks it.
2. The behavioural tests call the real `wait_for_pipeline` and let the real
   `_clamp_timeout` run, so a pass-through of the long budget — a NAME the static
   scanner below cannot resolve — is still caught.
3. The static test finds its subjects FROM THE CALL SITES, by AST, rather than from a
   hand-listed set of constants. A new `poll_job` call site added tomorrow with a
   differently-named constant is covered the day it is written, with no edit here.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from w2v_common.clients.base import _MAX_TIMEOUT, _clamp_timeout

from src.clients.orchestrator_client import (
    _MIN_POLL_SLICE_SECONDS,
    _POLL_SLICE_SECONDS,
    OrchestratorClient,
)

#: The long overall budget the dispatcher/course-manager stack is configured with
#: (`asgi.py::lesson_timeout`, `CourseManager`, `LessonDispatcher` all default to it).
#: It is intentionally far above the client ceiling.
_LESSON_BUDGET_SECONDS = 1800.0


@pytest.fixture
def orch_client() -> OrchestratorClient:
    return OrchestratorClient(base_url="http://orchestrator.test:9100")


# --------------------------------------------------------------------------------
# 1. Behavioural: the REAL clamp runs, on the REAL long budget.
# --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_pipeline_accepts_the_full_lesson_budget(
    orch_client: OrchestratorClient,
) -> None:
    """The configured 1800s lesson budget must not raise out of the client clamp.

    `_request` is mocked (no network), but `poll_job` — and therefore
    `_clamp_timeout` — is the real library code. Passing the budget straight to
    `poll_job` makes this raise `ValueError`, which is the original defect.
    """
    with patch.object(orch_client, "_request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"status": "completed", "video_url": "https://x"}

        result = await orch_client.wait_for_pipeline("pipe-001", timeout=_LESSON_BUDGET_SECONDS)

    assert result["status"] == "completed"
    mock_req.assert_awaited_once_with("GET", "/api/pipeline/pipe-001")


@pytest.mark.asyncio
async def test_every_timeout_reaching_poll_job_is_accepted_by_the_library(
    orch_client: OrchestratorClient,
) -> None:
    """Spy on `poll_job` and re-validate each deadline through the library itself.

    Asserting via `_clamp_timeout` rather than `<= 600` means this tracks the library
    if the ceiling moves, in EITHER direction.
    """
    seen: list[float] = []

    async def _spy(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["timeout"])
        return {"status": "completed"}

    with patch.object(orch_client, "poll_job", side_effect=_spy):
        await orch_client.wait_for_pipeline("pipe-001", timeout=_LESSON_BUDGET_SECONDS)

    assert seen, "wait_for_pipeline never reached poll_job"
    for value in seen:
        _clamp_timeout(value)  # raises ValueError if this service would blow up


@pytest.mark.asyncio
async def test_the_long_budget_is_never_handed_to_a_client_method(
    orch_client: OrchestratorClient,
) -> None:
    """Option (b)'s defining property: the budget stays in the local loop.

    The budget is spent, not delegated. If any single `poll_job` call carried the
    whole 1800s, the clamp would reject it — so assert the separation directly
    rather than inferring it.
    """
    seen: list[float] = []

    async def _spy(**kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs["timeout"])
        return {"status": "completed"}

    with patch.object(orch_client, "poll_job", side_effect=_spy):
        await orch_client.wait_for_pipeline("pipe-001", timeout=_LESSON_BUDGET_SECONDS)

    assert _LESSON_BUDGET_SECONDS not in seen
    assert max(seen) <= _MAX_TIMEOUT


@pytest.mark.asyncio
async def test_slice_constants_are_within_the_library_bounds() -> None:
    """Both local constants are bounded by the library, not by a copied number."""
    _clamp_timeout(_POLL_SLICE_SECONDS)
    _clamp_timeout(_MIN_POLL_SLICE_SECONDS)


# --------------------------------------------------------------------------------
# 2. Behavioural: the long budget is genuinely PRESERVED, not silently truncated.
# --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lesson_outliving_one_slice_keeps_polling(
    orch_client: OrchestratorClient,
) -> None:
    """A job still running after a slice expires must be re-polled, not failed.

    This is the whole justification for option (b) over lowering the constants to
    600.0: a lesson that legitimately needs more than one slice must not be
    abandoned. Two slices time out, the third completes.
    """
    calls = 0

    async def _spy(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise TimeoutError("slice expired, job still running")
        return {"status": "completed", "video_url": "https://x"}

    with patch.object(orch_client, "poll_job", side_effect=_spy):
        result = await orch_client.wait_for_pipeline("pipe-001", timeout=_LESSON_BUDGET_SECONDS)

    assert calls == 3
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_budget_exhaustion_still_raises_timeout_error(
    orch_client: OrchestratorClient,
) -> None:
    """The loop must terminate on the overall deadline, not spin forever."""

    async def _always_timeout(**kwargs: Any) -> dict[str, Any]:
        raise TimeoutError("slice expired")

    with (
        patch.object(orch_client, "poll_job", side_effect=_always_timeout),
        pytest.raises(TimeoutError, match="did not complete within"),
    ):
        # A budget below one slice: exactly one attempt, then the deadline.
        await orch_client.wait_for_pipeline("pipe-001", timeout=2.0)


@pytest.mark.asyncio
async def test_downstream_failure_propagates_without_burning_the_budget(
    orch_client: OrchestratorClient,
) -> None:
    """A failed pipeline is terminal — the slice loop must not retry it."""
    calls = 0

    async def _spy(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise RuntimeError("orchestrator job pipe-001 failed: boom")

    with (
        patch.object(orch_client, "poll_job", side_effect=_spy),
        pytest.raises(RuntimeError, match="boom"),
    ):
        await orch_client.wait_for_pipeline("pipe-001", timeout=_LESSON_BUDGET_SECONDS)

    assert calls == 1


# --------------------------------------------------------------------------------
# 3. Static: every literal deadline at a `poll_job` call site anywhere in src/.
# --------------------------------------------------------------------------------

_SRC_DIR = Path("src")

#: Method names that reach `BaseServiceClient.poll_job`'s clamp. `wait_for_pipeline`
#: is deliberately NOT here: it now owns a budget loop and takes an unclamped value.
#: Anything added to this tuple must be a method whose `timeout=` is clamped.
_CLAMPED_POLL_METHODS = ("poll_job",)


def _resolve(expr: ast.expr, module_name: str) -> float | None:
    """The value production actually sends for this `timeout=` expression.

    A bare literal resolves directly; a NAME resolves through the IMPORTED module
    attribute rather than the source text, so a constant computed from other
    constants resolves to its real value. A pass-through parameter or an attribute
    (`self._lesson_timeout`) resolves to None and is left to the behavioural tests
    above, which run the real clamp.
    """
    if isinstance(expr, ast.Constant):
        return float(expr.value) if isinstance(expr.value, int | float) else None
    if isinstance(expr, ast.Name):
        resolved = getattr(importlib.import_module(module_name), expr.id, None)
        return float(resolved) if isinstance(resolved, int | float) else None
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "min":
        values = [_resolve(arg, module_name) for arg in expr.args]
        known = [v for v in values if v is not None]
        # `min(remaining, _POLL_SLICE_SECONDS)`: the resolvable bound is the ceiling
        # this call can ever reach, which is exactly what must satisfy the clamp.
        return min(known) if known else None
    return None


def _poll_timeout_sites() -> list[tuple[str, str, float]]:
    """`(module, expression_source, resolved_value)` for every clamped deadline."""
    sites: list[tuple[str, str, float]] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_name = ".".join(path.with_suffix("").parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in _CLAMPED_POLL_METHODS:
                continue
            for kw in node.keywords:
                if kw.arg != "timeout":
                    continue
                value = _resolve(kw.value, module_name)
                if value is not None:
                    sites.append((module_name, ast.unparse(kw.value), value))
    return sites


def test_the_static_scan_actually_found_call_sites() -> None:
    """A scan that finds nothing would pass vacuously forever."""
    assert _poll_timeout_sites(), (
        "no clamped poll deadlines found in src/ — the AST scan has gone blind, "
        "which would make the test below vacuous"
    )


def test_every_clamped_poll_deadline_in_src_is_within_the_library_ceiling() -> None:
    offenders = []
    for module_name, source, value in _poll_timeout_sites():
        try:
            _clamp_timeout(value)
        except ValueError as exc:
            offenders.append(f"{module_name}: timeout={source} -> {value} ({exc})")

    assert not offenders, (
        "these poll deadlines raise ValueError out of BaseServiceClient before the "
        f"first HTTP call (ceiling is {_MAX_TIMEOUT}s):\n  " + "\n  ".join(offenders)
    )
