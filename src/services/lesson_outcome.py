"""Shared vocabulary for what a lesson pipeline actually delivered.

One place defines what each ``Lesson.pipeline_status`` value *means* so that
the dispatcher, the progress tracker and the course/module verdicts cannot
drift apart. They did drift: ``LessonDispatcher._execute_lesson`` returned the
literal ``"completed"`` for any status the shared ``poll_job`` accepted
(``completed``, ``completed_with_errors``, ``partial``), while
``ProgressTracker`` counted only ``completed`` and
``CourseManager._update_module_statuses`` treated anything outside
``{completed, failed, skipped}`` as "still running" — so one lesson could be a
win, a non-event and an in-flight job at the same time.
"""

from __future__ import annotations

from typing import Final

# A video exists and the pipeline reported no errors producing it.
DELIVERED_STATUSES: Final[frozenset[str]] = frozenset({"completed"})

# The pipeline returned something, but not a clean deliverable: it reported
# errors, only produced part of the lesson, or reported success without a
# ``video_url``. Terminal, but NOT a delivery.
DEGRADED_STATUSES: Final[frozenset[str]] = frozenset({"partial", "completed_with_errors"})

# Terminal with nothing to show for it.
LOST_STATUSES: Final[frozenset[str]] = frozenset({"failed", "skipped"})

TERMINAL_STATUSES: Final[frozenset[str]] = DELIVERED_STATUSES | DEGRADED_STATUSES | LOST_STATUSES

# What a lesson that produced no clean video is recorded as. ``partial`` is
# already a status the orchestrator itself emits, so this adds no new value to
# the vocabulary — it only stops a degraded lesson being filed as a win.
DEGRADED = "partial"


def normalize_lesson_status(raw_status: str, *, video_url: str | None) -> str:
    """Map an orchestrator poll status onto this service's lesson vocabulary.

    ``poll_job`` returns for ``completed``, ``completed_with_errors`` and
    ``partial`` alike, so the raw status is the only thing that separates a
    delivered lesson from a degraded one. A ``completed`` job that carries no
    ``video_url`` is a measurement gap, not a delivery, and is recorded as
    degraded rather than silently counted as a win.
    """
    if raw_status in DELIVERED_STATUSES:
        return "completed" if video_url else DEGRADED
    if raw_status in DEGRADED_STATUSES:
        return DEGRADED
    return raw_status
