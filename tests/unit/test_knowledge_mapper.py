"""Tests for knowledge_mapper module."""

from __future__ import annotations

from typing import Any

from src.services.curriculum.knowledge_mapper import (
    _estimate_complexity,
    _extract_content_summary,
    _extract_subtopics,
    _extract_themes,
    extract_topics,
)


class TestExtractTopics:
    def test_extracts_topics_from_pages(self, sample_analysis: dict[str, Any]) -> None:
        result = extract_topics(sample_analysis)
        assert result["total_topics"] == 3
        assert result["total_pages"] == 3
        assert len(result["topics"]) == 3
        assert result["topics"][0]["title"] == "Getting Started"

    def test_deduplicates_titles(self) -> None:
        analysis = {
            "pages": [
                {"title": "Same Title", "url": "https://a.com"},
                {"title": "Same Title", "url": "https://b.com"},
                {"title": "Different", "url": "https://c.com"},
            ],
            "raw": {},
        }
        result = extract_topics(analysis)
        assert result["total_topics"] == 2

    def test_skips_empty_titles(self) -> None:
        analysis = {
            "pages": [
                {"title": "", "url": "https://a.com"},
                {"title": "Valid", "url": "https://b.com"},
            ],
            "raw": {},
        }
        result = extract_topics(analysis)
        assert result["total_topics"] == 1

    def test_extracts_site_metadata(self, sample_analysis: dict[str, Any]) -> None:
        result = extract_topics(sample_analysis)
        assert result["site_name"] == "Example Platform"
        assert "developer" in result["site_description"].lower()

    def test_extracts_themes(self, sample_analysis: dict[str, Any]) -> None:
        result = extract_topics(sample_analysis)
        assert "developer tools" in result["key_themes"]

    def test_handles_empty_analysis(self) -> None:
        result = extract_topics({})
        assert result["total_topics"] == 0
        assert result["topics"] == []


class TestHelpers:
    def test_extract_content_summary(self) -> None:
        page = {"summary": "This is a summary"}
        assert _extract_content_summary(page) == "This is a summary"

    def test_extract_content_summary_fallback(self) -> None:
        page = {"description": "Fallback desc"}
        assert _extract_content_summary(page) == "Fallback desc"

    def test_extract_subtopics_from_headings(self) -> None:
        page = {"headings": ["H1", "H2", "H3"]}
        result = _extract_subtopics(page)
        assert result == ["H1", "H2", "H3"]

    def test_extract_subtopics_from_dict_headings(self) -> None:
        page = {"headings": [{"text": "Heading 1"}, {"text": "Heading 2"}]}
        result = _extract_subtopics(page)
        assert result == ["Heading 1", "Heading 2"]

    def test_extract_subtopics_from_sections(self) -> None:
        """Sections with dict entries containing 'title' are extracted."""
        page = {
            "headings": ["H1"],
            "sections": [
                {"title": "Section A"},
                {"title": "Section B"},
                {"title": "H1"},  # duplicate of heading, should be skipped
                {"title": ""},  # empty, should be skipped
                "not-a-dict",  # non-dict, should be skipped
            ],
        }
        result = _extract_subtopics(page)
        assert "H1" in result
        assert "Section A" in result
        assert "Section B" in result
        assert "" not in result
        # H1 should appear only once (dedup)
        assert result.count("H1") == 1

    def test_estimate_complexity_beginner(self) -> None:
        page = {"text_content": "short " * 50}
        assert _estimate_complexity(page) == "beginner"

    def test_estimate_complexity_intermediate(self) -> None:
        page = {"text_content": "word " * 900}
        assert _estimate_complexity(page) == "intermediate"

    def test_estimate_complexity_advanced(self) -> None:
        page = {"text_content": "word " * 2500}
        assert _estimate_complexity(page) == "advanced"

    def test_extract_themes_from_list(self) -> None:
        narrative = {"key_themes": ["theme1", "theme2"]}
        assert _extract_themes(narrative) == ["theme1", "theme2"]

    def test_extract_themes_from_dicts(self) -> None:
        narrative = {"key_themes": [{"name": "Theme A"}, {"theme": "Theme B"}]}
        assert _extract_themes(narrative) == ["Theme A", "Theme B"]
