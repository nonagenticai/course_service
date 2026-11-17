"""Tests for CourseRepository against a real postgres database.

Every test exercises actual SQL generation, ORM behaviour, and constraints
via the postgres+TRUNCATE fixture chain (engine/session_factory/repo) in
tests/conftest.py.
"""

from __future__ import annotations

from src.database.models import Course, CourseBuildLog, Lesson, Module

# ---------------------------------------------------------------------------
# Helpers - seed objects
# ---------------------------------------------------------------------------


def _make_course(
    course_id: str = "c1",
    tenant_id: str = "t1",
    status: str = "planning",
    **overrides,
) -> Course:
    defaults = {
        "course_id": course_id,
        "tenant_id": tenant_id,
        "status": status,
        "source_config": {"source_type": "web", "source_url": "https://example.com"},
        "config": {},
    }
    defaults.update(overrides)
    return Course(**defaults)


def _make_module(
    module_id: str = "m1",
    course_id: str = "c1",
    position: int = 0,
    title: str = "Module 1",
    **overrides,
) -> Module:
    defaults = {
        "module_id": module_id,
        "course_id": course_id,
        "position": position,
        "title": title,
    }
    defaults.update(overrides)
    return Module(**defaults)


def _make_lesson(
    lesson_id: str = "l1",
    module_id: str = "m1",
    course_id: str = "c1",
    position: int = 0,
    title: str = "Lesson 1",
    **overrides,
) -> Lesson:
    defaults = {
        "lesson_id": lesson_id,
        "module_id": module_id,
        "course_id": course_id,
        "position": position,
        "title": title,
    }
    defaults.update(overrides)
    return Lesson(**defaults)


# ---------------------------------------------------------------------------
# Course operations
# ---------------------------------------------------------------------------


class TestCreateCourse:
    async def test_adds_and_returns_course(self, repo):
        course = _make_course()
        result = await repo.create_course(course)

        assert result.course_id == "c1"
        assert result.tenant_id == "t1"
        assert result.status == "planning"

    async def test_created_course_is_retrievable(self, repo):
        await repo.create_course(_make_course())
        fetched = await repo.get_course("c1", tenant_id="t1")

        assert fetched is not None
        assert fetched.course_id == "c1"

    async def test_jsonb_source_config_round_trips(self, repo):
        cfg = {"source_type": "web", "source_url": "https://example.com", "depth": 3}
        await repo.create_course(_make_course(source_config=cfg))
        fetched = await repo.get_course("c1", tenant_id="t1")

        assert fetched is not None
        assert fetched.source_config["depth"] == 3


class TestGetCourse:
    async def test_returns_course_when_found(self, repo):
        await repo.create_course(_make_course())
        result = await repo.get_course("c1", tenant_id="t1")

        assert result is not None
        assert result.course_id == "c1"

    async def test_returns_none_when_not_found(self, repo):
        result = await repo.get_course("missing", tenant_id="t1")
        assert result is None


class TestGetCourseWithModules:
    async def test_returns_course_with_eager_load(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        result = await repo.get_course_with_modules("c1", tenant_id="t1")

        assert result is not None
        assert len(result.modules) == 1
        assert result.modules[0].module_id == "m1"
        assert len(result.modules[0].lessons) == 1
        assert result.modules[0].lessons[0].lesson_id == "l1"

    async def test_returns_none_when_missing(self, repo):
        result = await repo.get_course_with_modules("nope", tenant_id="t1")
        assert result is None


class TestListCourses:
    async def test_returns_courses_and_count(self, repo):
        await repo.create_course(_make_course("c1"))
        await repo.create_course(_make_course("c2"))
        await repo.create_course(_make_course("c3", tenant_id="other"))

        courses, total = await repo.list_courses("t1", limit=10, offset=0)

        assert total == 2
        assert len(courses) == 2
        assert all(c.tenant_id == "t1" for c in courses)

    async def test_pagination_limit(self, repo):
        for i in range(5):
            await repo.create_course(_make_course(f"c{i}"))

        courses, total = await repo.list_courses("t1", limit=2, offset=0)

        assert total == 5
        assert len(courses) == 2

    async def test_pagination_offset(self, repo):
        for i in range(5):
            await repo.create_course(_make_course(f"c{i}"))

        courses, total = await repo.list_courses("t1", limit=10, offset=3)

        assert total == 5
        assert len(courses) == 2

    async def test_empty_tenant_returns_zero(self, repo):
        courses, total = await repo.list_courses("no-tenant")
        assert total == 0
        assert courses == []


class TestUpdateCourse:
    async def test_updates_and_returns_course(self, repo):
        await repo.create_course(_make_course())

        result = await repo.update_course("c1", tenant_id="t1", status="completed", title="Done")

        assert result is not None
        assert result.status == "completed"
        assert result.title == "Done"

    async def test_update_persists_to_db(self, repo):
        await repo.create_course(_make_course())
        await repo.update_course("c1", tenant_id="t1", status="building")

        fetched = await repo.get_course("c1", tenant_id="t1")
        assert fetched is not None
        assert fetched.status == "building"

    async def test_update_nonexistent_returns_none(self, repo):
        result = await repo.update_course("missing", tenant_id="t1", status="failed")
        assert result is None


class TestDeleteCourse:
    async def test_deletes_existing_course(self, repo):
        await repo.create_course(_make_course())
        result = await repo.delete_course("c1", tenant_id="t1")

        assert result is True

    async def test_deleted_course_is_gone(self, repo):
        await repo.create_course(_make_course())
        await repo.delete_course("c1", tenant_id="t1")

        fetched = await repo.get_course("c1", tenant_id="t1")
        assert fetched is None

    async def test_returns_false_when_not_found(self, repo):
        result = await repo.delete_course("missing", tenant_id="t1")
        assert result is False


# ---------------------------------------------------------------------------
# Module operations
# ---------------------------------------------------------------------------


class TestCreateModules:
    async def test_bulk_creates_modules(self, repo):
        await repo.create_course(_make_course())
        modules = [
            _make_module("m1", position=0, title="Mod 1"),
            _make_module("m2", position=1, title="Mod 2"),
        ]
        result = await repo.create_modules(modules)

        assert len(result) == 2
        assert result[0].module_id == "m1"
        assert result[1].module_id == "m2"

    async def test_modules_are_retrievable_after_create(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])

        fetched = await repo.get_module("m1")
        assert fetched is not None
        assert fetched.title == "Module 1"


class TestGetModule:
    async def test_returns_module(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])

        result = await repo.get_module("m1")
        assert result is not None
        assert result.module_id == "m1"

    async def test_returns_none_when_missing(self, repo):
        result = await repo.get_module("missing")
        assert result is None


class TestGetModules:
    async def test_returns_ordered_modules(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules(
            [
                _make_module("m2", position=1, title="Second"),
                _make_module("m1", position=0, title="First"),
            ]
        )

        result = await repo.get_modules("c1", tenant_id="t1")

        assert len(result) == 2
        assert result[0].position == 0
        assert result[0].title == "First"
        assert result[1].position == 1
        assert result[1].title == "Second"

    async def test_returns_empty_for_unknown_course(self, repo):
        result = await repo.get_modules("nonexistent", tenant_id="t1")
        assert result == []


class TestUpdateModule:
    async def test_updates_and_returns(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])

        result = await repo.update_module("m1", title="Updated Title", status="completed")

        assert result is not None
        assert result.title == "Updated Title"
        assert result.status == "completed"

    async def test_update_persists(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.update_module("m1", title="New Title")

        fetched = await repo.get_module("m1")
        assert fetched is not None
        assert fetched.title == "New Title"

    async def test_update_nonexistent_returns_none(self, repo):
        result = await repo.update_module("missing", title="X")
        assert result is None


# ---------------------------------------------------------------------------
# Lesson operations
# ---------------------------------------------------------------------------


class TestCreateLessons:
    async def test_bulk_creates_lessons(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        lessons = [_make_lesson("l1", position=0), _make_lesson("l2", position=1)]
        result = await repo.create_lessons(lessons)

        assert len(result) == 2

    async def test_lessons_are_retrievable(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        fetched = await repo.get_lesson("l1")
        assert fetched is not None
        assert fetched.title == "Lesson 1"


class TestGetLesson:
    async def test_returns_lesson(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        result = await repo.get_lesson("l1")
        assert result is not None
        assert result.lesson_id == "l1"

    async def test_returns_none_when_missing(self, repo):
        result = await repo.get_lesson("missing")
        assert result is None


class TestUpdateLesson:
    async def test_updates_and_returns(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        result = await repo.update_lesson("l1", pipeline_status="completed", video_url="http://v")

        assert result is not None
        assert result.pipeline_status == "completed"
        assert result.video_url == "http://v"

    async def test_update_persists(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])
        await repo.update_lesson("l1", pipeline_status="failed", error="boom")

        fetched = await repo.get_lesson("l1")
        assert fetched is not None
        assert fetched.pipeline_status == "failed"
        assert fetched.error == "boom"

    async def test_update_nonexistent_returns_none(self, repo):
        result = await repo.update_lesson("missing", pipeline_status="x")
        assert result is None


class TestGetLessonsByCourse:
    async def test_returns_ordered_lessons(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules(
            [
                _make_module("m1", position=0),
                _make_module("m2", position=1),
            ]
        )
        await repo.create_lessons(
            [
                _make_lesson("l2", module_id="m2", position=0, title="L2"),
                _make_lesson("l1", module_id="m1", position=0, title="L1"),
            ]
        )

        result = await repo.get_lessons_by_course("c1", tenant_id="t1")

        assert len(result) == 2
        # Ordered by module_id then position
        assert result[0].module_id == "m1"
        assert result[1].module_id == "m2"

    async def test_returns_empty_for_unknown_course(self, repo):
        result = await repo.get_lessons_by_course("nonexistent", tenant_id="t1")
        assert result == []


class TestGetLessonsForModule:
    async def test_returns_module_lessons(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module("m1"), _make_module("m2", position=1)])
        await repo.create_lessons(
            [
                _make_lesson("l1", module_id="m1", position=0),
                _make_lesson("l2", module_id="m1", position=1),
                _make_lesson("l3", module_id="m2", position=0),
            ]
        )

        result = await repo.get_lessons_for_module("m1")

        assert len(result) == 2
        assert result[0].lesson_id == "l1"
        assert result[1].lesson_id == "l2"

    async def test_returns_empty_for_unknown_module(self, repo):
        result = await repo.get_lessons_for_module("nonexistent")
        assert result == []


class TestGetLessonsByStatus:
    async def test_filters_by_status(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons(
            [
                _make_lesson("l1", position=0, pipeline_status="completed"),
                _make_lesson("l2", position=1, pipeline_status="failed"),
                _make_lesson("l3", position=2, pipeline_status="completed"),
            ]
        )

        result = await repo.get_lessons_by_status("c1", "completed", tenant_id="t1")

        assert len(result) == 2
        assert all(ln.pipeline_status == "completed" for ln in result)

    async def test_returns_empty_when_no_match(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson(pipeline_status="pending")])

        result = await repo.get_lessons_by_status("c1", "failed", tenant_id="t1")
        assert result == []


class TestIncrementLessonRetry:
    async def test_increments_retry(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        await repo.increment_lesson_retry("l1")

        fetched = await repo.get_lesson("l1")
        assert fetched is not None
        assert fetched.retry_count == 1

    async def test_increments_multiple_times(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons([_make_lesson()])

        await repo.increment_lesson_retry("l1")
        await repo.increment_lesson_retry("l1")
        await repo.increment_lesson_retry("l1")

        fetched = await repo.get_lesson("l1")
        assert fetched is not None
        assert fetched.retry_count == 3


# ---------------------------------------------------------------------------
# Build log operations
# ---------------------------------------------------------------------------


class TestAddBuildLog:
    async def test_adds_log(self, repo):
        await repo.create_course(_make_course())
        log = CourseBuildLog(log_id="log1", course_id="c1", event_type="started")
        result = await repo.add_build_log(log)

        assert result.log_id == "log1"
        assert result.event_type == "started"

    async def test_log_with_details_round_trips(self, repo):
        await repo.create_course(_make_course())
        details = {"step": "curriculum", "retries": 2}
        log = CourseBuildLog(
            log_id="log2", course_id="c1", event_type="step_complete", details=details
        )
        await repo.add_build_log(log)

        logs = await repo.get_build_logs("c1", tenant_id="t1")
        assert len(logs) == 1
        assert logs[0].details["step"] == "curriculum"


class TestGetBuildLogs:
    async def test_returns_logs(self, repo):
        await repo.create_course(_make_course())
        await repo.add_build_log(
            CourseBuildLog(log_id="log1", course_id="c1", event_type="started")
        )
        await repo.add_build_log(
            CourseBuildLog(log_id="log2", course_id="c1", event_type="completed")
        )

        result = await repo.get_build_logs("c1", tenant_id="t1")
        assert len(result) == 2

    async def test_respects_limit(self, repo):
        await repo.create_course(_make_course())
        for i in range(10):
            await repo.add_build_log(
                CourseBuildLog(log_id=f"log{i}", course_id="c1", event_type=f"ev{i}")
            )

        result = await repo.get_build_logs("c1", tenant_id="t1", limit=3)
        assert len(result) == 3

    async def test_returns_empty_for_unknown_course(self, repo):
        result = await repo.get_build_logs("nonexistent", tenant_id="t1")
        assert result == []


# ---------------------------------------------------------------------------
# Aggregate operations
# ---------------------------------------------------------------------------


class TestUpdateCourseProgress:
    async def test_recalculates_progress(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons(
            [
                _make_lesson("l1", position=0, pipeline_status="completed"),
                _make_lesson("l2", position=1, pipeline_status="completed"),
                _make_lesson("l3", position=2, pipeline_status="completed"),
                _make_lesson("l4", position=3, pipeline_status="failed"),
                _make_lesson("l5", position=4, pipeline_status="failed"),
                _make_lesson("l6", position=5, pipeline_status="skipped"),
                _make_lesson("l7", position=6, pipeline_status="pending"),
                _make_lesson("l8", position=7, pipeline_status="pending"),
                _make_lesson("l9", position=8, pipeline_status="pending"),
                _make_lesson("l10", position=9, pipeline_status="pending"),
            ]
        )

        await repo.update_course_progress("c1", tenant_id="t1")

        course = await repo.get_course("c1", tenant_id="t1")
        assert course is not None
        assert course.total_lessons == 10
        assert course.completed_lessons == 3
        assert course.failed_lessons == 2
        assert course.skipped_lessons == 1
        assert course.progress_pct == 30  # 3/10 * 100

    async def test_zero_total_gives_zero_pct(self, repo):
        await repo.create_course(_make_course())

        await repo.update_course_progress("c1", tenant_id="t1")

        course = await repo.get_course("c1", tenant_id="t1")
        assert course is not None
        assert course.total_lessons == 0
        assert course.progress_pct == 0

    async def test_all_completed_gives_100_pct(self, repo):
        await repo.create_course(_make_course())
        await repo.create_modules([_make_module()])
        await repo.create_lessons(
            [
                _make_lesson("l1", position=0, pipeline_status="completed"),
                _make_lesson("l2", position=1, pipeline_status="completed"),
            ]
        )

        await repo.update_course_progress("c1", tenant_id="t1")

        course = await repo.get_course("c1", tenant_id="t1")
        assert course is not None
        assert course.progress_pct == 100
        assert course.completed_lessons == 2
        assert course.total_lessons == 2


class TestCountCourses:
    async def test_returns_count(self, repo):
        await repo.create_course(_make_course("c1"))
        await repo.create_course(_make_course("c2"))
        await repo.create_course(_make_course("c3", tenant_id="other"))

        result = await repo.count_courses("t1")
        assert result == 2

    async def test_returns_zero_for_unknown_tenant(self, repo):
        result = await repo.count_courses("nobody")
        assert result == 0


class TestListIncompleteCourses:
    async def test_returns_non_terminal_courses(self, repo):
        await repo.create_course(_make_course("c1", status="planning"))
        await repo.create_course(_make_course("c2", status="building"))
        await repo.create_course(_make_course("c3", status="completed"))
        await repo.create_course(_make_course("c4", status="failed"))

        result = await repo.list_incomplete_courses()

        ids = {c.course_id for c in result}
        assert ids == {"c1", "c2"}
