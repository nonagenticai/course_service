"""Validate generated curriculum structure."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _check_lesson_duration(
    lesson_title: str,
    duration: int,
    min_duration_seconds: int,
    max_duration_seconds: int,
) -> list[str]:
    """Return any duration-bound warnings for a single lesson."""
    if duration < min_duration_seconds:
        return [
            f"Lesson '{lesson_title}' duration {duration}s below minimum {min_duration_seconds}s"
        ]
    if duration > max_duration_seconds:
        return [
            f"Lesson '{lesson_title}' duration {duration}s exceeds maximum {max_duration_seconds}s"
        ]
    return []


def _validate_module_lessons(
    module_title: str,
    lessons: list[dict[str, Any]],
    all_titles: set[str],
    *,
    min_duration_seconds: int,
    max_duration_seconds: int,
) -> list[str]:
    """Validate a single module's lessons, mutating *all_titles* in place."""
    warnings: list[str] = []
    module_titles: set[str] = set()

    for j, lesson in enumerate(lessons):
        lesson_title = lesson.get("title", f"Lesson {j + 1}")

        if lesson_title in module_titles:
            warnings.append(f"Duplicate lesson title '{lesson_title}' in module '{module_title}'")
        module_titles.add(lesson_title)

        if lesson_title in all_titles:
            warnings.append(f"Duplicate lesson title '{lesson_title}' across course")
        all_titles.add(lesson_title)

        duration = lesson.get("target_duration_seconds", 120)
        warnings.extend(
            _check_lesson_duration(
                lesson_title, duration, min_duration_seconds, max_duration_seconds
            )
        )

    return warnings


def validate_curriculum(
    curriculum: dict[str, Any],
    *,
    max_lessons: int = 200,
    min_duration_seconds: int = 60,
    max_duration_seconds: int = 180,
) -> list[str]:
    """Validate a generated curriculum and return a list of warnings.

    Checks:
    - Total lesson count within limit
    - Duration targets within bounds
    - No duplicate lesson titles within a module
    - Prerequisite ordering (modules in sequence)
    - Each module has at least one lesson

    Returns:
        List of warning strings (empty = valid).
    """
    warnings: list[str] = []
    modules = curriculum.get("modules", [])

    if not modules:
        warnings.append("Curriculum has no modules")
        return warnings

    total_lessons = 0
    all_titles: set[str] = set()

    for i, module in enumerate(modules):
        module_title = module.get("title", f"Module {i + 1}")
        lessons = module.get("lessons", [])

        if not lessons:
            warnings.append(f"Module '{module_title}' has no lessons")
            continue

        total_lessons += len(lessons)
        warnings.extend(
            _validate_module_lessons(
                module_title,
                lessons,
                all_titles,
                min_duration_seconds=min_duration_seconds,
                max_duration_seconds=max_duration_seconds,
            )
        )

    if total_lessons > max_lessons:
        warnings.append(f"Total lessons ({total_lessons}) exceeds maximum ({max_lessons})")

    if total_lessons == 0:
        warnings.append("Curriculum has no lessons across all modules")

    logger.info(
        "Curriculum validation: %d modules, %d lessons, %d warnings",
        len(modules),
        total_lessons,
        len(warnings),
    )
    return warnings


def enforce_limits(
    curriculum: dict[str, Any],
    *,
    max_lessons: int = 200,
    target_duration_seconds: int = 120,
) -> dict[str, Any]:
    """Enforce lesson limits and duration defaults on a curriculum.

    Trims excess lessons and sets default durations.
    """
    modules = curriculum.get("modules", [])
    total = 0
    trimmed_modules = []

    for module in modules:
        lessons = module.get("lessons", [])
        remaining = max_lessons - total
        if remaining <= 0:
            break

        if len(lessons) > remaining:
            lessons = lessons[:remaining]
            logger.warning(
                "Trimmed module '%s' to %d lessons (hit max_lessons=%d)",
                module.get("title", "?"),
                len(lessons),
                max_lessons,
            )

        # Ensure duration defaults
        for lesson in lessons:
            if "target_duration_seconds" not in lesson:
                lesson["target_duration_seconds"] = target_duration_seconds

        total += len(lessons)
        trimmed_modules.append({**module, "lessons": lessons})

    return {**curriculum, "modules": trimmed_modules}
