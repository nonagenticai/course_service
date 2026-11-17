"""Unit tests for course_service database models."""

from src.database.models import Course, CourseBuildLog, Lesson, Module


class TestCourseModel:
    def test_create_course_minimal(self):
        course = Course(
            course_id="test-id",
            tenant_id="tenant-1",
            status="planning",
            source_config={"source_type": "web", "url": "https://example.com"},
            progress_pct=0,
            total_lessons=0,
            completed_lessons=0,
            failed_lessons=0,
            skipped_lessons=0,
        )
        assert course.course_id == "test-id"
        assert course.tenant_id == "tenant-1"
        assert course.status == "planning"
        assert course.progress_pct == 0

    def test_course_tablename(self):
        assert Course.__tablename__ == "courses"

    def test_course_with_all_fields(self):
        course = Course(
            course_id="test-id",
            tenant_id="tenant-1",
            status="producing",
            title="Python Course",
            description="Learn Python",
            source_config={"source_type": "web"},
            source_analysis={"pages": []},
            curriculum={"modules": []},
            config={"llm_model": "test-model"},
            progress_pct=50,
            total_lessons=10,
            completed_lessons=5,
            failed_lessons=0,
            skipped_lessons=0,
        )
        assert course.title == "Python Course"
        assert course.progress_pct == 50

    def test_course_jsonb_fields_accept_dicts(self):
        course = Course(
            course_id="test-id",
            tenant_id="tenant-1",
            source_config={"key": "value"},
            source_analysis={"data": [1, 2, 3]},
            curriculum={"modules": [{"title": "Mod 1"}]},
            manifest={"version": 1},
            config={"llm_model": "gpt-4"},
        )
        assert course.source_config == {"key": "value"}
        assert course.curriculum == {"modules": [{"title": "Mod 1"}]}
        assert course.manifest == {"version": 1}

    def test_course_relationships_exist(self):
        # Verify relationship attributes are defined on the class
        assert hasattr(Course, "modules")
        assert hasattr(Course, "build_logs")


class TestModuleModel:
    def test_create_module(self):
        module = Module(
            module_id="mod-1",
            course_id="course-1",
            position=0,
            title="Introduction",
            status="pending",
        )
        assert module.module_id == "mod-1"
        assert module.status == "pending"

    def test_module_tablename(self):
        assert Module.__tablename__ == "modules"

    def test_module_relationships_exist(self):
        assert hasattr(Module, "lessons")
        assert hasattr(Module, "course")


class TestLessonModel:
    def test_create_lesson(self):
        lesson = Lesson(
            lesson_id="les-1",
            module_id="mod-1",
            course_id="course-1",
            position=0,
            title="Hello World",
            pipeline_status="pending",
            retry_count=0,
            video_type="mixed",
            target_duration_seconds=120,
        )
        assert lesson.lesson_id == "les-1"
        assert lesson.pipeline_status == "pending"
        assert lesson.retry_count == 0
        assert lesson.video_type == "mixed"
        assert lesson.target_duration_seconds == 120

    def test_lesson_tablename(self):
        assert Lesson.__tablename__ == "lessons"


class TestCourseBuildLogModel:
    def test_create_log(self):
        log = CourseBuildLog(
            log_id="log-1",
            course_id="course-1",
            event_type="course_started",
        )
        assert log.event_type == "course_started"
        assert log.lesson_id is None

    def test_log_tablename(self):
        assert CourseBuildLog.__tablename__ == "course_build_logs"
