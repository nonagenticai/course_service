"""Tests for src.services.curriculum.planner.CurriculumPlanner."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.services.curriculum.planner import CurriculumPlanner


@pytest.fixture
def sample_analysis() -> dict[str, Any]:
    return {
        "pages": [
            {
                "title": "Getting Started",
                "url": "https://example.com/start",
                "page_type": "content",
                "summary": "Intro guide",
                "headings": ["Overview", "Setup"],
            },
        ],
        "raw": {
            "site_analysis": {
                "site_name": "TestSite",
                "description": "A test site",
            },
            "narrative_extraction": {
                "key_themes": ["testing", "automation"],
            },
        },
        "site_analysis": {
            "site_name": "TestSite",
            "description": "A test site",
        },
    }


@pytest.fixture
def llm_curriculum() -> dict[str, Any]:
    """Curriculum dict as returned by StructureGenerator.generate()."""
    return {
        "title": "TestSite Mastery",
        "description": "Full course on TestSite",
        "modules": [
            {
                "title": "Basics",
                "description": "Getting started",
                "lessons": [
                    {
                        "title": "Lesson A",
                        "topic": "Topic A",
                        "description": "Desc A",
                        "target_duration_seconds": 120,
                    },
                    {
                        "title": "Lesson B",
                        "topic": "Topic B",
                        "description": "Desc B",
                        "target_duration_seconds": 120,
                    },
                ],
            },
        ],
    }


class TestCurriculumPlanner:
    """Tests for CurriculumPlanner.plan()."""

    async def test_plan_orchestrates_full_pipeline(
        self,
        sample_analysis: dict[str, Any],
        llm_curriculum: dict[str, Any],
    ) -> None:
        planner = CurriculumPlanner()

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = llm_curriculum

            result = await planner.plan(sample_analysis)

        # Verify generator was called exactly once
        mock_gen.assert_awaited_once()

        # Verify knowledge_map structure passed to generator
        call_kwargs = mock_gen.call_args
        knowledge_map = call_kwargs.args[0]
        assert "topics" in knowledge_map
        assert len(knowledge_map["topics"]) == 1

        topic = knowledge_map["topics"][0]
        assert topic["title"] == "Getting Started"
        assert topic["page_url"] == "https://example.com/start"
        assert topic["complexity"] in ("beginner", "intermediate", "advanced")
        assert "id" in topic
        assert topic["id"].startswith("topic-")

        # Verify enforce_limits was applied: lessons have duration defaults set
        for module in result["modules"]:
            for lesson in module["lessons"]:
                assert "target_duration_seconds" in lesson

        # Verify validate_curriculum was called: warnings key present in result
        assert "warnings" in result
        assert isinstance(result["warnings"], list)

        # Verify top-level curriculum keys
        assert result["title"] == "TestSite Mastery"
        assert "modules" in result

    async def test_plan_passes_user_context(
        self,
        sample_analysis: dict[str, Any],
        llm_curriculum: dict[str, Any],
    ) -> None:
        planner = CurriculumPlanner()
        user_ctx = {"tone": "casual", "audience": "beginners"}

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = llm_curriculum

            result = await planner.plan(sample_analysis, user_context=user_ctx)

        # Verify user_context was forwarded to the generator
        _, kwargs = mock_gen.call_args
        assert kwargs["user_context"] == user_ctx

        # Verify the pipeline completed (warnings key proves validate ran)
        assert "warnings" in result

    async def test_plan_enforces_lesson_limits(
        self,
        sample_analysis: dict[str, Any],
    ) -> None:
        planner = CurriculumPlanner()

        # Return a curriculum with 6 lessons across 2 modules
        big_curriculum = {
            "title": "Big",
            "description": "Too many lessons",
            "modules": [
                {
                    "title": "M1",
                    "lessons": [
                        {"title": f"L{i}", "target_duration_seconds": 120} for i in range(3)
                    ],
                },
                {
                    "title": "M2",
                    "lessons": [
                        {"title": f"L{i + 3}", "target_duration_seconds": 120} for i in range(3)
                    ],
                },
            ],
        }

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = big_curriculum

            result = await planner.plan(sample_analysis, max_lessons=4)

        total = sum(len(m["lessons"]) for m in result["modules"])
        assert total <= 4

    async def test_plan_returns_validation_warnings(
        self,
        sample_analysis: dict[str, Any],
    ) -> None:
        planner = CurriculumPlanner()

        # Curriculum with a lesson that has too-short duration
        curriculum_with_issue = {
            "title": "Warn",
            "description": "Has issues",
            "modules": [
                {
                    "title": "M1",
                    "lessons": [
                        {"title": "Short", "target_duration_seconds": 30},
                    ],
                },
            ],
        }

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = curriculum_with_issue

            result = await planner.plan(sample_analysis)

        assert len(result["warnings"]) > 0
        assert any("below minimum" in w for w in result["warnings"])

    async def test_plan_with_empty_analysis(self) -> None:
        """When analysis has no pages, knowledge map has zero topics."""
        planner = CurriculumPlanner()
        empty_analysis: dict[str, Any] = {"pages": [], "raw": {}}

        # Generator still returns a curriculum (the LLM may produce something generic)
        empty_curriculum: dict[str, Any] = {
            "title": "Empty",
            "description": "No content",
            "modules": [
                {
                    "title": "Placeholder",
                    "lessons": [{"title": "Intro", "target_duration_seconds": 120}],
                },
            ],
        }

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = empty_curriculum

            result = await planner.plan(empty_analysis)

        # Verify knowledge map had zero topics
        knowledge_map = mock_gen.call_args.args[0]
        assert knowledge_map["total_topics"] == 0
        assert knowledge_map["topics"] == []

        # Pipeline still completes
        assert "warnings" in result
        assert "modules" in result

    async def test_plan_with_generator_failure(
        self,
        sample_analysis: dict[str, Any],
    ) -> None:
        """When the LLM generator raises, the exception propagates."""
        planner = CurriculumPlanner()

        with patch.object(planner._generator, "generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.side_effect = RuntimeError("Curriculum generation failed after 3 attempts")

            with pytest.raises(RuntimeError, match="Curriculum generation failed"):
                await planner.plan(sample_analysis)
