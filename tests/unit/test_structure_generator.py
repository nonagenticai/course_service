"""Tests for src.services.curriculum.structure_generator.StructureGenerator."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from w2v_common.clients import WMClient
from w2v_common.clients.wm_client import WMResponse

from src.services.curriculum.structure_generator import (
    _CURRICULUM_OUTPUT_SCHEMA,
    _SYSTEM_PROMPT,
    _WORKER_NAME,
    StructureGenerator,
)


def _wm_response(output: object) -> WMResponse:
    """Build a WMResponse with the given parsed output payload."""
    return WMResponse(
        output=output,
        output_type="object",
        output_schema_used=_CURRICULUM_OUTPUT_SCHEMA,
        metadata=None,
        usage_metrics=None,
    )


def _build_generator(
    *,
    return_value: Any = None,
    side_effect: Any = None,
    model_override: str | None = None,
) -> tuple[StructureGenerator, AsyncMock]:
    """Construct a StructureGenerator backed by a mocked WMClient."""
    mock_wm = AsyncMock(spec=WMClient)
    if side_effect is not None:
        mock_wm.message = AsyncMock(side_effect=side_effect)
    else:
        mock_wm.message = AsyncMock(return_value=return_value)
    gen = StructureGenerator(wm_client=mock_wm, model_override=model_override)
    return gen, mock_wm.message


@pytest.fixture
def generator() -> StructureGenerator:
    """Generator with a mocked WMClient (parse-only tests don't call message)."""
    gen, _ = _build_generator(return_value=_wm_response({"title": "x", "modules": [{}]}))
    return gen


class TestParseCurriculum:
    """Tests for ``_parse_curriculum`` normalization logic."""

    def test_parse_valid_dict(self, generator: StructureGenerator) -> None:
        output = {
            "title": "My Course",
            "description": "A great course",
            "modules": [
                {
                    "title": "Module 1",
                    "lessons": [{"title": "Lesson 1"}],
                }
            ],
        }
        result = generator._parse_curriculum(output)
        assert result["title"] == "My Course"
        assert result["description"] == "A great course"
        assert len(result["modules"]) == 1
        assert result["modules"][0]["title"] == "Module 1"

    def test_parse_alternative_key_sections(self, generator: StructureGenerator) -> None:
        output = {
            "title": "Alt Keys",
            "description": "Using sections key",
            "sections": [
                {"title": "Section 1", "lessons": [{"title": "L1"}]},
            ],
        }
        result = generator._parse_curriculum(output)
        assert len(result["modules"]) == 1
        assert result["modules"][0]["title"] == "Section 1"

    def test_parse_alternative_key_chapters(self, generator: StructureGenerator) -> None:
        output = {
            "course_title": "Chapters Course",
            "course_description": "Uses chapters",
            "chapters": [
                {"title": "Ch 1", "lessons": [{"title": "L1"}]},
            ],
        }
        result = generator._parse_curriculum(output)
        assert result["title"] == "Chapters Course"
        assert result["description"] == "Uses chapters"
        assert len(result["modules"]) == 1

    def test_parse_alternative_key_course_modules(self, generator: StructureGenerator) -> None:
        output = {
            "title": "CM",
            "description": "course_modules key",
            "course_modules": [
                {"title": "CM1", "lessons": [{"title": "L1"}]},
            ],
        }
        result = generator._parse_curriculum(output)
        assert len(result["modules"]) == 1
        assert result["modules"][0]["title"] == "CM1"

    def test_parse_raises_on_non_dict(self, generator: StructureGenerator) -> None:
        with pytest.raises(ValueError, match="Expected dict"):
            generator._parse_curriculum("this is a string, not a dict")

    def test_parse_raises_on_list(self, generator: StructureGenerator) -> None:
        with pytest.raises(ValueError, match="Expected dict"):
            generator._parse_curriculum([{"title": "M", "lessons": []}])

    def test_parse_raises_when_no_modules_found(self, generator: StructureGenerator) -> None:
        with pytest.raises(ValueError, match="No modules found"):
            generator._parse_curriculum({"title": "Empty", "description": "No modules"})

    def test_parse_raises_when_modules_list_empty(self, generator: StructureGenerator) -> None:
        with pytest.raises(ValueError, match="No modules found"):
            generator._parse_curriculum(
                {"title": "Empty", "description": "Empty modules list", "modules": []}
            )


class TestGenerate:
    async def test_generate_success(self) -> None:
        good_output = {
            "title": "Generated",
            "description": "Generated curriculum",
            "modules": [{"title": "M1", "lessons": [{"title": "L1"}]}],
        }
        gen, message_mock = _build_generator(return_value=_wm_response(good_output))

        result = await gen.generate(
            {"topics": [{"id": "t1", "title": "Topic"}], "site_name": "Test"},
            max_lessons=10,
        )
        assert result["title"] == "Generated"
        assert len(result["modules"]) == 1

        # Verify the WM call shape: worker_name, system_prompt, output_schema.
        message_mock.assert_awaited_once()
        call = message_mock.await_args
        assert call is not None
        assert call.kwargs["worker_name"] == _WORKER_NAME
        assert call.kwargs["system_prompt"] == _SYSTEM_PROMPT
        assert call.kwargs["output_schema"] == _CURRICULUM_OUTPUT_SCHEMA
        assert call.kwargs["model_override"] is None
        # ``message`` is the rendered prompt — non-empty string.
        assert isinstance(call.kwargs["message"], str)
        assert len(call.kwargs["message"]) > 0

    async def test_generate_passes_model_override(self) -> None:
        good_output = {
            "title": "X",
            "modules": [{"title": "M", "lessons": [{"title": "L"}]}],
        }
        gen, message_mock = _build_generator(
            return_value=_wm_response(good_output), model_override="some-other-model"
        )

        await gen.generate({"topics": [], "site_name": "T"}, max_lessons=5)

        call = message_mock.await_args
        assert call is not None
        assert call.kwargs["model_override"] == "some-other-model"

    async def test_retries_on_shape_error(self) -> None:
        # First response: dict but missing modules → ValueError → retry.
        # Second response: well-formed.
        bad = _wm_response({"title": "Bad", "description": "no modules"})
        good = _wm_response(
            {"title": "Retry", "modules": [{"title": "M", "lessons": [{"title": "L"}]}]}
        )
        gen, message_mock = _build_generator(side_effect=[bad, good])

        with patch(
            "src.services.curriculum.structure_generator.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await gen.generate({"topics": [], "site_name": "Test"}, max_lessons=10)

        assert result["title"] == "Retry"
        assert message_mock.await_count == 2

    async def test_raises_after_max_retries(self) -> None:
        bad = _wm_response({"title": "Bad", "description": "no modules"})
        gen, message_mock = _build_generator(return_value=bad)

        with (
            patch(
                "src.services.curriculum.structure_generator.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(RuntimeError, match="failed after"),
        ):
            await gen.generate({"topics": [], "site_name": "Test"}, max_lessons=5)

        assert message_mock.await_count == 3

    async def test_retries_on_transport_exception(self) -> None:
        good = _wm_response(
            {"title": "After Error", "modules": [{"title": "M", "lessons": [{"title": "L"}]}]}
        )
        gen, _ = _build_generator(side_effect=[ConnectionError("transient"), good])

        with patch(
            "src.services.curriculum.structure_generator.asyncio.sleep", new_callable=AsyncMock
        ):
            result = await gen.generate({"topics": [], "site_name": "Test"}, max_lessons=10)
        assert result["title"] == "After Error"

    async def test_non_dict_output_raises(self) -> None:
        # WM returned a string (e.g. provider ignored output_schema).
        gen, _ = _build_generator(return_value=_wm_response("not a dict"))

        with (
            patch(
                "src.services.curriculum.structure_generator.asyncio.sleep",
                new_callable=AsyncMock,
            ),
            pytest.raises(RuntimeError, match="failed after"),
        ):
            await gen.generate({"topics": [], "site_name": "X"}, max_lessons=5)


class TestBuildPrompt:
    def test_renders_template(self, generator: StructureGenerator) -> None:
        prompt = generator._build_prompt(
            {"site_name": "MySite", "topics": [], "key_themes": ["AI"]},
            max_lessons=50,
            target_lesson_duration_seconds=120,
            user_context={"audience": "beginners"},
        )
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert "MySite" in prompt
