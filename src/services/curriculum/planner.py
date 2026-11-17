"""Orchestrate curriculum planning: knowledge mapping -> structure generation -> validation."""

from __future__ import annotations

import logging
from typing import Any

from w2v_common.clients import WMClient

from src.services.curriculum.knowledge_mapper import extract_topics
from src.services.curriculum.structure_generator import StructureGenerator
from src.services.curriculum.validator import enforce_limits, validate_curriculum

logger = logging.getLogger(__name__)


class CurriculumPlanner:
    """Orchestrate the full curriculum planning pipeline."""

    def __init__(
        self,
        *,
        wm_client: WMClient | None = None,
        model_override: str | None = None,
        timeout_seconds: int = 120,
        max_retry_attempts: int = 3,
    ) -> None:
        self._generator = StructureGenerator(
            wm_client=wm_client,
            model_override=model_override,
            timeout_seconds=timeout_seconds,
            max_retry_attempts=max_retry_attempts,
        )

    async def plan(
        self,
        analysis: dict[str, Any],
        *,
        max_lessons: int = 200,
        target_lesson_duration_seconds: int = 120,
        user_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a validated curriculum from analysis data.

        Args:
            analysis: Analysis data from analysis_service.
            max_lessons: Maximum number of lessons.
            target_lesson_duration_seconds: Target duration per lesson.
            user_context: Optional user preferences.

        Returns:
            Validated curriculum dict with title, description, modules, and warnings.
        """
        # Step 1: Extract knowledge map
        knowledge_map = extract_topics(analysis)
        logger.info("CurriculumPlanner: extracted %d topics", len(knowledge_map.get("topics", [])))

        # Step 2: Generate structure via LLM
        curriculum = await self._generator.generate(
            knowledge_map,
            max_lessons=max_lessons,
            target_lesson_duration_seconds=target_lesson_duration_seconds,
            user_context=user_context,
        )

        # Step 3: Enforce limits
        curriculum = enforce_limits(
            curriculum,
            max_lessons=max_lessons,
            target_duration_seconds=target_lesson_duration_seconds,
        )

        # Step 4: Validate
        warnings = validate_curriculum(
            curriculum,
            max_lessons=max_lessons,
        )
        for w in warnings:
            logger.warning("Curriculum validation: %s", w)

        curriculum["warnings"] = warnings

        total_lessons = sum(len(m.get("lessons", [])) for m in curriculum.get("modules", []))
        logger.info(
            "CurriculumPlanner: curriculum ready — %d modules, %d lessons, %d warnings",
            len(curriculum.get("modules", [])),
            total_lessons,
            len(warnings),
        )
        return curriculum
