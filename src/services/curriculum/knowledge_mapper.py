"""Extract structured knowledge from analysis data for curriculum planning."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_topics(analysis: dict[str, Any]) -> dict[str, Any]:
    """Extract topics, subtopics, prerequisites, and complexity from analysis JSON.

    Args:
        analysis: Raw analysis data from analysis_service, expected to contain
                  pages, site_analysis, and optionally narrative_extraction.

    Returns:
        Knowledge map with topics, subtopics, and metadata.
    """
    pages = analysis.get("pages", [])
    raw = analysis.get("raw", {}) if isinstance(analysis.get("raw"), dict) else {}
    site_analysis = analysis.get("site_analysis") or raw.get("site_analysis") or {}
    narrative = analysis.get("narrative_extraction") or raw.get("narrative_extraction") or {}

    topics: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for i, page in enumerate(pages):
        title = page.get("title", "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        topic: dict[str, Any] = {
            "id": f"topic-{i + 1:03d}",
            "title": title,
            "page_url": page.get("url", ""),
            "page_type": page.get("page_type", "content"),
            "content_summary": _extract_content_summary(page),
            "subtopics": _extract_subtopics(page),
            "complexity": _estimate_complexity(page),
        }
        topics.append(topic)

    knowledge_map = {
        "site_name": site_analysis.get("site_name", ""),
        "site_description": site_analysis.get("description", ""),
        "total_pages": len(pages),
        "total_topics": len(topics),
        "topics": topics,
        "key_themes": _extract_themes(narrative),
    }

    logger.info(
        "Knowledge mapper: extracted %d topics from %d pages",
        len(topics),
        len(pages),
    )
    return knowledge_map


def _extract_content_summary(page: dict[str, Any]) -> str:
    """Extract a content summary from a page."""
    for key in ("summary", "description", "content_summary", "text_content"):
        val = page.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
    return ""


def _extract_subtopics(page: dict[str, Any]) -> list[str]:
    """Extract subtopics from page headings or sections."""
    subtopics = []
    headings = page.get("headings", [])
    if isinstance(headings, list):
        for h in headings[:10]:
            if isinstance(h, str):
                subtopics.append(h.strip())
            elif isinstance(h, dict):
                text = h.get("text", "").strip()
                if text:
                    subtopics.append(text)

    sections = page.get("sections", [])
    if isinstance(sections, list):
        for s in sections[:10]:
            if isinstance(s, dict):
                title = s.get("title", "").strip()
                if title and title not in subtopics:
                    subtopics.append(title)

    return subtopics


def _estimate_complexity(page: dict[str, Any]) -> str:
    """Estimate topic complexity based on page metadata."""
    content = page.get("text_content", "") or page.get("content", "")
    word_count = len(str(content).split()) if content else 0
    subtopic_count = len(_extract_subtopics(page))

    if word_count > 2000 or subtopic_count > 8:
        return "advanced"
    if word_count > 800 or subtopic_count > 4:
        return "intermediate"
    return "beginner"


def _extract_themes(narrative: dict[str, Any]) -> list[str]:
    """Extract key themes from narrative extraction data."""
    themes: list[str] = []

    for key in ("key_themes", "themes", "main_topics"):
        val = narrative.get(key)
        if isinstance(val, list):
            for t in val[:10]:
                if isinstance(t, str):
                    themes.append(t)
                elif isinstance(t, dict):
                    name = t.get("name") or t.get("theme") or t.get("topic", "")
                    if name:
                        themes.append(str(name))

    return themes
