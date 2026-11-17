"""Tests for curriculum validator module."""

from __future__ import annotations

from typing import Any

from src.services.curriculum.validator import enforce_limits, validate_curriculum


class TestValidateCurriculum:
    def test_valid_curriculum(self, sample_curriculum: dict[str, Any]) -> None:
        warnings = validate_curriculum(sample_curriculum)
        assert len(warnings) == 0

    def test_empty_modules(self) -> None:
        warnings = validate_curriculum({"modules": []})
        assert "no modules" in warnings[0].lower()

    def test_empty_lessons_in_module(self) -> None:
        curriculum = {"modules": [{"title": "Empty Module", "lessons": []}]}
        warnings = validate_curriculum(curriculum)
        assert any("no lessons" in w.lower() for w in warnings)

    def test_duplicate_lesson_title_within_module(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [
                        {"title": "Same Title", "target_duration_seconds": 120},
                        {"title": "Same Title", "target_duration_seconds": 120},
                    ],
                }
            ]
        }
        warnings = validate_curriculum(curriculum)
        assert any("duplicate" in w.lower() for w in warnings)

    def test_duplicate_lesson_title_across_modules(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "Shared Title"}],
                },
                {
                    "title": "Module 2",
                    "lessons": [{"title": "Shared Title"}],
                },
            ]
        }
        warnings = validate_curriculum(curriculum)
        assert any("across course" in w.lower() for w in warnings)

    def test_exceeds_max_lessons(self) -> None:
        lessons = [{"title": f"Lesson {i}"} for i in range(10)]
        curriculum = {"modules": [{"title": "Big Module", "lessons": lessons}]}
        warnings = validate_curriculum(curriculum, max_lessons=5)
        assert any("exceeds maximum" in w.lower() for w in warnings)

    def test_duration_below_minimum(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "Short", "target_duration_seconds": 30}],
                }
            ]
        }
        warnings = validate_curriculum(curriculum, min_duration_seconds=60)
        assert any("below minimum" in w.lower() for w in warnings)

    def test_duration_above_maximum(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "Long", "target_duration_seconds": 300}],
                }
            ]
        }
        warnings = validate_curriculum(curriculum, max_duration_seconds=180)
        assert any("exceeds maximum" in w.lower() for w in warnings)


class TestEnforceLimits:
    def test_trims_excess_lessons(self) -> None:
        lessons = [{"title": f"Lesson {i}"} for i in range(10)]
        curriculum = {"modules": [{"title": "Module 1", "lessons": lessons}]}
        result = enforce_limits(curriculum, max_lessons=5)
        total = sum(len(m["lessons"]) for m in result["modules"])
        assert total == 5

    def test_sets_default_duration(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "No Duration"}],
                }
            ]
        }
        result = enforce_limits(curriculum, target_duration_seconds=90)
        lesson = result["modules"][0]["lessons"][0]
        assert lesson["target_duration_seconds"] == 90

    def test_preserves_existing_duration(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "Has Duration", "target_duration_seconds": 150}],
                }
            ]
        }
        result = enforce_limits(curriculum, target_duration_seconds=120)
        lesson = result["modules"][0]["lessons"][0]
        assert lesson["target_duration_seconds"] == 150

    def test_trims_across_modules(self) -> None:
        curriculum = {
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": f"L{i}"} for i in range(4)],
                },
                {
                    "title": "Module 2",
                    "lessons": [{"title": f"L{i + 4}"} for i in range(4)],
                },
            ]
        }
        result = enforce_limits(curriculum, max_lessons=6)
        total = sum(len(m["lessons"]) for m in result["modules"])
        assert total == 6
        assert len(result["modules"][0]["lessons"]) == 4
        assert len(result["modules"][1]["lessons"]) == 2
