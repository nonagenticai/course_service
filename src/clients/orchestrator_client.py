"""HTTP client for the w2v_orchestrator pipeline API."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.clients.base import BaseServiceClient

logger = logging.getLogger(__name__)

#: One `poll_job` call's deadline, in seconds.
#:
#: `BaseServiceClient.poll_job` opens with `timeout = _clamp_timeout(timeout)`, and
#: `_clamp_timeout` RAISES `ValueError` above its ceiling (`_MAX_TIMEOUT = 600.0`,
#: overridable by `CLIENT_MAX_TIMEOUT_S`, which is set in no repo and in no gitops
#: manifest). It does NOT silently cap. So handing the client a lesson-sized deadline
#: blows up before the first GET, on every call — it is not a tuning mistake that
#: merely fails to help.
#:
#: A lesson pipeline (script -> narrate -> compile), dispatched ten-wide against a
#: shared orchestrator, routinely outlives 600s of wall clock. Lowering the lesson
#: budget to the transport ceiling would therefore abandon lessons that are still
#: running and mark them failed. Instead the long budget is spent as a sequence of
#: slices, each one a deadline the client accepts. This is the shape
#: `w2v_orchestrator`'s `audiobook/narrate.py::_GATEWAY_POLL_BUDGET_SECONDS = 7200.0`
#: uses: the budget lives in a local `time.monotonic()` loop, never in a client kwarg.
_POLL_SLICE_SECONDS = 300.0

#: Do not open a slice shorter than the client's own floor (`_MIN_TIMEOUT = 1.0`);
#: `_clamp_timeout` raises below it just as it does above the ceiling.
_MIN_POLL_SLICE_SECONDS = 1.0


class OrchestratorClient(BaseServiceClient):
    """Client for dispatching lesson pipelines to the orchestrator."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 30.0,
        license_token: str | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            service_name="orchestrator",
            timeout=timeout,
            license_token=license_token,
        )

    async def create_pipeline(
        self,
        *,
        mode: str = "lesson",
        url: str | None = None,
        prior_results: dict[str, Any] | None = None,
        user_context: dict[str, Any] | None = None,
        callback_url: str | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Submit a pipeline job to the orchestrator.

        Returns the response dict containing at minimum {"job_id": ...}.
        """
        payload: dict[str, Any] = {
            "config": {"mode": mode, **(config_overrides or {})},
        }
        if url:
            payload["url"] = url
        if prior_results:
            payload["prior_results"] = prior_results
        if user_context:
            payload["user_context"] = user_context
        if callback_url:
            payload["callback_url"] = callback_url

        logger.info("orchestrator: submitting %s pipeline", mode)
        result = await self._request("POST", "/api/pipeline", json=payload)
        logger.info("orchestrator: pipeline created job_id=%s", result.get("job_id"))
        return result

    async def get_pipeline_status(self, job_id: str) -> dict[str, Any]:
        """Get current status of a pipeline job."""
        return await self._request("GET", f"/api/pipeline/{job_id}")

    async def wait_for_pipeline(
        self,
        job_id: str,
        timeout: float = 1800.0,
    ) -> dict[str, Any]:
        """Poll a pipeline job until completion.

        Args:
            job_id: Orchestrator pipeline job to wait on.
            timeout: OVERALL budget in seconds. This is deliberately allowed to
                exceed the client's per-call ceiling: it is spent as a sequence of
                `_POLL_SLICE_SECONDS` slices rather than handed to `poll_job` whole
                (see `_POLL_SLICE_SECONDS`). Never pass this value to a client
                method directly.

        Raises:
            TimeoutError: The job did not reach a terminal status within `timeout`.
            RuntimeError: The orchestrator reported the job as failed (raised from
                the slice that observed it, without waiting out the budget).
        """
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining < _MIN_POLL_SLICE_SECONDS:
                break
            try:
                return await self.poll_job(
                    endpoint="/api/pipeline",
                    job_id=job_id,
                    interval=5.0,
                    timeout=min(remaining, _POLL_SLICE_SECONDS),
                )
            except TimeoutError:
                # This slice expired (or spent its attempt budget) while the job was
                # still running. That is the expected shape for a long lesson, not a
                # failure — only the overall deadline above ends the wait. A genuine
                # orchestrator-side failure surfaces as RuntimeError and propagates.
                logger.debug(
                    "orchestrator: job %s still running, %.0fs of budget left",
                    job_id,
                    max(deadline - time.monotonic(), 0.0),
                )

        raise TimeoutError(f"orchestrator pipeline {job_id} did not complete within {timeout}s")
