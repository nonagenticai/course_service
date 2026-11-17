"""Generate course structure (modules + lessons) via the WM LLM gateway.

w2v services NEVER call openai/anthropic/inference-router directly — every
LLM call goes through ``worker_management.POST /workers/{name}/message``
so cost is attributed per tenant and providers can be swapped server-side.
See memory ``wm-as-sole-llm-gateway.md`` for the canonical pattern.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import jinja2
from w2v_common.clients import WMClient

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"

_WORKER_NAME = "course-curriculum-generator"

_SYSTEM_PROMPT = (
    "You are a course curriculum architect. Given a website analysis, you "
    "produce a structured course as a single JSON object with keys "
    '"title" (string), "description" (string), and "modules" (array). '
    "Each module is an object with `title`, `description`, and a `lessons` "
    "array; each lesson is an object with `title`, `topic`, `description`, "
    "`video_type`, and `target_duration_seconds`. Output ONLY the JSON "
    "object — no prose, no markdown fences."
)

# Output schema passed to WM for structured-output mode. This describes the
# expected JSON shape; WM forwards it to the underlying provider as OpenAI
# response_format=json_schema so the model returns a parsed dict directly
# via WMResponse.output instead of a stringified blob we'd have to parse
# client-side.
_CURRICULUM_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "lessons": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "topic": {"type": "string"},
                                "description": {"type": "string"},
                                "video_type": {"type": "string"},
                                "target_duration_seconds": {"type": "integer"},
                            },
                            "required": ["title"],
                        },
                    },
                },
                "required": ["title", "lessons"],
            },
        },
    },
    "required": ["title", "modules"],
}


class StructureGenerator:
    """Generate a structured curriculum from a knowledge map via WM."""

    def __init__(
        self,
        *,
        wm_client: WMClient | None = None,
        model_override: str | None = None,
        timeout_seconds: int = 120,
        max_retry_attempts: int = 3,
    ) -> None:
        # One shared WMClient per generator — its underlying httpx.AsyncClient
        # is connection-pooled and the identity event hook reads request-scoped
        # ContextVars, so the same instance serves concurrent end-user
        # requests correctly.
        self._wm = wm_client if wm_client is not None else WMClient(timeout=float(timeout_seconds))
        self._model_override = model_override
        self._timeout = timeout_seconds
        self._max_retries = max_retry_attempts

        env = jinja2.Environment(  # nosec B701
            loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,  # noqa: S701 -- renders LLM prompts, not HTML
        )
        self._template = env.get_template("curriculum_prompt.md")

    async def generate(
        self,
        knowledge_map: dict[str, Any],
        *,
        max_lessons: int = 200,
        target_lesson_duration_seconds: int = 120,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a structured curriculum from a knowledge map.

        Returns:
            Dict with keys: title, description, modules (each with lessons).
        """
        t0 = time.monotonic()
        logger.info(
            "StructureGenerator: generating curriculum, topics=%d, max_lessons=%d",
            len(knowledge_map.get("topics", [])),
            max_lessons,
        )

        prompt = self._build_prompt(
            knowledge_map, max_lessons, target_lesson_duration_seconds, user_context
        )
        result = await self._call_llm_with_retry(prompt)

        elapsed = time.monotonic() - t0
        module_count = len(result.get("modules", []))
        lesson_count = sum(len(m.get("lessons", [])) for m in result.get("modules", []))
        logger.info(
            "StructureGenerator: completed in %.2fs, %d modules, %d lessons",
            elapsed,
            module_count,
            lesson_count,
        )
        return result

    def _build_prompt(
        self,
        knowledge_map: dict[str, Any],
        max_lessons: int,
        target_lesson_duration_seconds: int,
        user_context: dict[str, Any] | None,
    ) -> str:
        """Render the curriculum prompt template."""
        return self._template.render(
            site_name=knowledge_map.get("site_name", ""),
            site_description=knowledge_map.get("site_description", ""),
            topics=knowledge_map.get("topics", []),
            key_themes=knowledge_map.get("key_themes", []),
            max_lessons=max_lessons,
            target_duration_seconds=target_lesson_duration_seconds,
            target_duration_minutes=target_lesson_duration_seconds / 60,
            user_context=user_context or {},
        )

    async def _call_llm_with_retry(self, prompt: str) -> dict[str, Any]:
        """Call WM with retry. WM returns parsed structured output."""
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                logger.info(
                    "StructureGenerator: WM call attempt %d/%d",
                    attempt,
                    self._max_retries,
                )
                response = await self._wm.message(
                    worker_name=_WORKER_NAME,
                    message=prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    output_schema=_CURRICULUM_OUTPUT_SCHEMA,
                    model_override=self._model_override,
                )
                return self._parse_curriculum(response.output)

            except (ValueError, TypeError, KeyError) as exc:
                # Shape errors from a provider that ignored the schema
                # (e.g. returned a list, scalar, or missing modules key).
                last_exc = exc
                logger.warning(
                    "StructureGenerator: attempt %d/%d shape error: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(attempt * 2)
                    continue
                raise RuntimeError(
                    f"Curriculum generation failed after {self._max_retries} attempts"
                ) from exc

            except (
                TimeoutError,
                ConnectionError,
                OSError,
                RuntimeError,
            ) as exc:
                # Transport / WM-side / runtime errors — retry with backoff.
                last_exc = exc
                logger.warning(
                    "StructureGenerator: attempt %d/%d failed: %s",
                    attempt,
                    self._max_retries,
                    exc,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(attempt * 2)
                    continue
                raise RuntimeError(
                    f"Curriculum generation failed after {self._max_retries} attempts"
                ) from exc

        raise RuntimeError(
            f"Curriculum generation: all {self._max_retries} attempts exhausted"
        ) from last_exc

    def _parse_curriculum(self, output: Any) -> dict[str, Any]:
        """Normalize a WM ``output`` payload into our curriculum dict.

        WM returns ``output`` as a dict when ``output_schema`` is set (the
        provider responded with structured output). Tolerate alternative
        keys like ``sections`` / ``chapters`` / ``course_modules`` that
        weaker models still emit despite the schema, and reject missing /
        empty modules so the retry loop can have another go.
        """
        if not isinstance(output, dict):
            raise ValueError(f"Expected dict from WM, got {type(output).__name__}")

        modules = output.get("modules", [])
        if not isinstance(modules, list) or not modules:
            for key in ("sections", "chapters", "parts", "course_modules"):
                if key in output and isinstance(output[key], list) and output[key]:
                    modules = output[key]
                    break

        if not modules:
            raise ValueError("No modules found in WM response")

        return {
            "title": output.get("title", output.get("course_title", "")),
            "description": output.get("description", output.get("course_description", "")),
            "modules": modules,
        }
