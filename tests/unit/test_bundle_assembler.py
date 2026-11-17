"""Tests for bundle_assembler module."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.database.models import Course, Lesson, Module
from src.services.bundle_assembler import BundleAssembler


@pytest.fixture
def assembler(mock_repository: AsyncMock) -> BundleAssembler:
    return BundleAssembler(mock_repository)


class TestBuildManifest:
    async def test_builds_manifest(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
        sample_course: Course,
        sample_module: Module,
        sample_lessons: list[Lesson],
    ) -> None:
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = [sample_module]
        mock_repository.get_lessons.return_value = sample_lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        # Verify top-level structure
        assert manifest["course_id"] == "course-001"
        assert manifest["title"] == sample_course.title
        assert manifest["description"] == sample_course.description
        assert manifest["total_modules"] == 1
        assert manifest["total_lessons"] == 3

        # Verify module structure
        assert len(manifest["modules"]) == 1
        mod_manifest = manifest["modules"][0]
        assert mod_manifest["module_id"] == "mod-001"
        assert mod_manifest["title"] == "Introduction"
        assert mod_manifest["description"] == "Getting started"
        assert mod_manifest["position"] == 1

        # Only 2 of 3 lessons belong to mod-001 (lesson-003 is mod-002)
        assert len(mod_manifest["lessons"]) == 2

        # Verify lesson fields
        first_lesson = mod_manifest["lessons"][0]
        assert first_lesson["lesson_id"] == "lesson-001"
        assert first_lesson["title"] == "Platform Overview"
        assert first_lesson["topic"] == "What is Example Platform"
        assert first_lesson["position"] == 1
        assert first_lesson["target_duration_seconds"] == 120
        assert first_lesson["pipeline_status"] == "pending"

        second_lesson = mod_manifest["lessons"][1]
        assert second_lesson["lesson_id"] == "lesson-002"
        assert second_lesson["title"] == "Installation Guide"
        assert second_lesson["position"] == 2

        # Verify manifest was persisted
        mock_repository.update_course.assert_called_once()
        update_kwargs = mock_repository.update_course.call_args
        assert update_kwargs.args[0] == "course-001"
        assert update_kwargs.kwargs["manifest"] == manifest

    async def test_builds_manifest_with_multiple_modules(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
        sample_course: Course,
        sample_lessons: list[Lesson],
    ) -> None:
        """Verify lessons are grouped correctly across multiple modules."""
        mod1 = Module(
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Mod A",
            description="First module",
        )
        mod2 = Module(
            module_id="mod-002",
            course_id="course-001",
            position=2,
            title="Mod B",
            description="Second module",
        )
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = [mod1, mod2]
        mock_repository.get_lessons.return_value = sample_lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        assert manifest["total_modules"] == 2
        assert len(manifest["modules"]) == 2

        # mod-001 has lesson-001 and lesson-002
        assert len(manifest["modules"][0]["lessons"]) == 2
        assert manifest["modules"][0]["module_id"] == "mod-001"

        # mod-002 has lesson-003
        assert len(manifest["modules"][1]["lessons"]) == 1
        assert manifest["modules"][1]["module_id"] == "mod-002"
        assert manifest["modules"][1]["lessons"][0]["lesson_id"] == "lesson-003"
        assert (
            manifest["modules"][1]["lessons"][0]["video_url"]
            == "https://cdn.example.com/videos/lesson-003.mp4"
        )

    async def test_raises_for_missing_course(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        mock_repository.get_course.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await assembler.build_manifest("nonexistent", tenant_id="t1")

    async def test_lessons_sorted_by_position(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
        sample_course: Course,
        sample_module: Module,
    ) -> None:
        """Lessons delivered out of order should appear sorted by position."""
        lessons = [
            Lesson(
                lesson_id="l3",
                module_id="mod-001",
                course_id="course-001",
                position=3,
                title="Third",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l1",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="First",
                pipeline_status="completed",
            ),
            Lesson(
                lesson_id="l2",
                module_id="mod-001",
                course_id="course-001",
                position=2,
                title="Second",
                pipeline_status="pending",
            ),
        ]
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = [sample_module]
        mock_repository.get_lessons.return_value = lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")
        manifest_lessons = manifest["modules"][0]["lessons"]

        assert len(manifest_lessons) == 3
        assert manifest_lessons[0]["title"] == "First"
        assert manifest_lessons[0]["position"] == 1
        assert manifest_lessons[1]["title"] == "Second"
        assert manifest_lessons[1]["position"] == 2
        assert manifest_lessons[2]["title"] == "Third"
        assert manifest_lessons[2]["position"] == 3

    async def test_module_with_no_lessons(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
        sample_course: Course,
    ) -> None:
        """A module with zero lessons should produce an empty lessons array."""
        empty_mod = Module(
            module_id="mod-empty",
            course_id="course-001",
            position=1,
            title="Empty Module",
            description="No lessons here",
        )
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = [empty_mod]
        mock_repository.get_lessons.return_value = []

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        assert manifest["total_modules"] == 1
        assert manifest["total_lessons"] == 0
        assert len(manifest["modules"]) == 1
        assert manifest["modules"][0]["lessons"] == []
        assert manifest["modules"][0]["module_id"] == "mod-empty"
        assert manifest["modules"][0]["title"] == "Empty Module"

    async def test_mixed_video_url_presence(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
        sample_course: Course,
        sample_module: Module,
    ) -> None:
        """Some lessons have video_url, others None -- both should appear correctly."""
        lessons = [
            Lesson(
                lesson_id="l-with-video",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="Has Video",
                pipeline_status="completed",
                video_url="https://cdn.example.com/v1.mp4",
            ),
            Lesson(
                lesson_id="l-no-video",
                module_id="mod-001",
                course_id="course-001",
                position=2,
                title="No Video",
                pipeline_status="pending",
                video_url=None,
            ),
        ]
        mock_repository.get_course.return_value = sample_course
        mock_repository.get_modules.return_value = [sample_module]
        mock_repository.get_lessons.return_value = lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")
        manifest_lessons = manifest["modules"][0]["lessons"]

        assert manifest_lessons[0]["video_url"] == "https://cdn.example.com/v1.mp4"
        assert manifest_lessons[0]["pipeline_status"] == "completed"
        assert manifest_lessons[1]["video_url"] is None
        assert manifest_lessons[1]["pipeline_status"] == "pending"


class TestManifestToJson:
    def test_serializes_to_json(self) -> None:
        manifest = {"course_id": "c1", "title": "Test"}
        result = BundleAssembler.manifest_to_json(manifest)
        parsed = json.loads(result)
        assert parsed == manifest

    def test_serializes_with_indent(self) -> None:
        manifest = {"key": "value"}
        result = BundleAssembler.manifest_to_json(manifest)
        # json.dumps with indent=2 produces multi-line output
        assert "\n" in result


class TestUploadBundle:
    async def test_returns_none_without_storage_settings(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        assembler = BundleAssembler(mock_repository, storage_settings=None)
        result = await assembler.upload_bundle("c1", {"manifest": True}, tenant_id="t1")
        assert result is None

    async def test_returns_none_without_credentials(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000", minio_access_key="", minio_secret_key=""
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)
        result = await assembler.upload_bundle("c1", {"manifest": True}, tenant_id="t1")
        assert result is None

    async def test_uploads_and_returns_url(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            minio_bucket="test-bucket",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        test_manifest = {"course_id": "c1", "title": "My Course", "modules": []}

        mock_minio = AsyncMock()
        mock_minio.bucket_exists.return_value = True
        mock_minio.put_object = AsyncMock()
        mock_minio.presigned_get_object = AsyncMock(
            return_value="https://minio:9000/test-bucket/c1/manifest.json?sig=abc"
        )

        with patch("miniopy_async.Minio", return_value=mock_minio):
            result = await assembler.upload_bundle("c1", test_manifest, tenant_id="t1")

        # Verify the presigned URL is returned
        assert result == "https://minio:9000/test-bucket/c1/manifest.json?sig=abc"

        # Verify put_object was called with correct parameters
        mock_minio.put_object.assert_called_once()
        put_call = mock_minio.put_object.call_args
        assert put_call.args[0] == "test-bucket"  # bucket
        assert put_call.args[1] == "c1/manifest.json"  # object_name

        # Verify the uploaded data is valid JSON matching the manifest
        uploaded_stream = put_call.args[2]
        uploaded_bytes = uploaded_stream.read()
        uploaded_json = json.loads(uploaded_bytes)
        assert uploaded_json == test_manifest

        assert put_call.kwargs["content_type"] == "application/json"
        assert put_call.kwargs["length"] == len(
            json.dumps(test_manifest, indent=2, default=str).encode("utf-8")
        )

        # Verify presigned_get_object was called with 7-day expiry
        presign_call = mock_minio.presigned_get_object.call_args
        assert presign_call.args[0] == "test-bucket"
        assert presign_call.args[1] == "c1/manifest.json"
        from datetime import timedelta

        assert presign_call.kwargs["expires"] == timedelta(days=7)

        # Verify bundle_url was persisted (tenant-scoped)
        mock_repository.update_course.assert_called_once_with(
            "c1",
            tenant_id="t1",
            bundle_url="https://minio:9000/test-bucket/c1/manifest.json?sig=abc",
        )

    async def test_creates_bucket_if_not_exists(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            minio_bucket="new-bucket",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        mock_minio = AsyncMock()
        mock_minio.bucket_exists.return_value = False
        mock_minio.make_bucket = AsyncMock()
        mock_minio.put_object = AsyncMock()
        mock_minio.presigned_get_object = AsyncMock(return_value="https://url")

        with patch("miniopy_async.Minio", return_value=mock_minio):
            await assembler.upload_bundle("c1", {}, tenant_id="t1")

        mock_minio.make_bucket.assert_called_once_with("new-bucket")

    async def test_handles_upload_error(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        with patch(
            "miniopy_async.Minio",
            side_effect=OSError("Connection refused"),
        ):
            result = await assembler.upload_bundle("c1", {}, tenant_id="t1")

        assert result is None

    async def test_object_name_format(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        """Verify the object name follows the {course_id}/manifest.json pattern."""
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            minio_bucket="bucket",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        mock_minio = AsyncMock()
        mock_minio.bucket_exists.return_value = True
        mock_minio.put_object = AsyncMock()
        mock_minio.presigned_get_object = AsyncMock(return_value="https://url")

        with patch("miniopy_async.Minio", return_value=mock_minio):
            await assembler.upload_bundle("my-course-123", {"data": True}, tenant_id="t1")

        put_call = mock_minio.put_object.call_args
        assert put_call.args[1] == "my-course-123/manifest.json"

    async def test_upload_handles_bucket_creation_failure(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        """When bucket_exists returns False and make_bucket raises, upload returns None."""
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            minio_bucket="bad-bucket",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        mock_minio = AsyncMock()
        mock_minio.bucket_exists.return_value = False
        mock_minio.make_bucket = AsyncMock(
            side_effect=OSError("Access denied: cannot create bucket")
        )

        with patch("miniopy_async.Minio", return_value=mock_minio):
            result = await assembler.upload_bundle("c1", {"manifest": True}, tenant_id="t1")

        assert result is None
        mock_minio.make_bucket.assert_called_once_with("bad-bucket")
        # put_object should never be called since make_bucket failed
        mock_minio.put_object.assert_not_called()

    async def test_upload_handles_presigned_url_failure(
        self,
        mock_repository: AsyncMock,
    ) -> None:
        """When put_object succeeds but presigned_get_object raises, upload returns None."""
        from unittest.mock import patch

        from w2v_common.config import StorageSettings

        settings = StorageSettings(
            minio_endpoint="minio:9000",
            minio_access_key="minioadmin",
            minio_secret_key="minioadmin",
            minio_bucket="test-bucket",
        )
        assembler = BundleAssembler(mock_repository, storage_settings=settings)

        mock_minio = AsyncMock()
        mock_minio.bucket_exists.return_value = True
        mock_minio.put_object = AsyncMock()
        mock_minio.presigned_get_object = AsyncMock(
            side_effect=ValueError("Presigned URL generation failed")
        )

        with patch("miniopy_async.Minio", return_value=mock_minio):
            result = await assembler.upload_bundle("c1", {"data": True}, tenant_id="t1")

        assert result is None
        # put_object was called successfully
        mock_minio.put_object.assert_called_once()
        # presigned_get_object was attempted
        mock_minio.presigned_get_object.assert_called_once()
        # bundle_url should NOT be persisted since we never got the URL
        mock_repository.update_course.assert_not_called()


class TestManifestNullFields:
    """Test manifest behavior with NULL/missing fields on models."""

    async def test_manifest_with_null_course_fields(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        """Course with title=None and description=None should produce manifest without crashing."""
        course = Course(
            course_id="course-null",
            tenant_id="test-tenant",
            status="planning",
            title=None,
            description=None,
            source_config={"source_type": "web", "source_url": "https://example.com"},
            config={},
        )
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = []
        mock_repository.get_lessons.return_value = []

        manifest = await assembler.build_manifest("course-null", tenant_id="t1")

        assert manifest["course_id"] == "course-null"
        assert manifest["title"] is None
        assert manifest["description"] is None
        assert manifest["total_modules"] == 0
        assert manifest["total_lessons"] == 0
        assert manifest["modules"] == []

        # Verify it serializes to JSON without errors
        json_str = BundleAssembler.manifest_to_json(manifest)
        parsed = json.loads(json_str)
        assert parsed["title"] is None
        assert parsed["description"] is None

    async def test_manifest_with_null_lesson_fields(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        """Lessons with topic=None, artifacts=None, video_url=None should appear as null."""
        course = Course(
            course_id="course-001",
            tenant_id="test-tenant",
            status="building",
            source_config={"source_type": "web"},
            config={},
        )
        module = Module(
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Module 1",
        )
        lessons = [
            Lesson(
                lesson_id="lesson-null",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="Null Fields Lesson",
                topic=None,
                video_url=None,
                artifacts=None,
                pipeline_status="pending",
            ),
        ]
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = [module]
        mock_repository.get_lessons.return_value = lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        lesson_manifest = manifest["modules"][0]["lessons"][0]
        assert lesson_manifest["lesson_id"] == "lesson-null"
        assert lesson_manifest["title"] == "Null Fields Lesson"
        assert lesson_manifest["topic"] is None
        assert lesson_manifest["video_url"] is None
        assert lesson_manifest["artifacts"] is None
        assert lesson_manifest["pipeline_status"] == "pending"

    async def test_manifest_preserves_lesson_metadata(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        """Verify ALL expected lesson fields appear in the manifest dict."""
        course = Course(
            course_id="course-001",
            tenant_id="test-tenant",
            status="completed",
            source_config={"source_type": "web"},
            config={},
        )
        module = Module(
            module_id="mod-001",
            course_id="course-001",
            position=1,
            title="Module 1",
        )
        lessons = [
            Lesson(
                lesson_id="lesson-full",
                module_id="mod-001",
                course_id="course-001",
                position=1,
                title="Full Metadata Lesson",
                topic="Advanced topic",
                target_duration_seconds=300,
                pipeline_status="completed",
                video_url="https://cdn.example.com/v.mp4",
                artifacts={"script": "s3://bucket/script.txt", "slides": "s3://bucket/slides.pdf"},
            ),
        ]
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = [module]
        mock_repository.get_lessons.return_value = lessons

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        lesson_manifest = manifest["modules"][0]["lessons"][0]

        # Verify ALL required keys are present
        expected_keys = {
            "lesson_id",
            "title",
            "topic",
            "position",
            "target_duration_seconds",
            "pipeline_status",
            "video_url",
            "artifacts",
        }
        assert set(lesson_manifest.keys()) == expected_keys

        # Verify values match
        assert lesson_manifest["lesson_id"] == "lesson-full"
        assert lesson_manifest["title"] == "Full Metadata Lesson"
        assert lesson_manifest["topic"] == "Advanced topic"
        assert lesson_manifest["position"] == 1
        assert lesson_manifest["target_duration_seconds"] == 300
        assert lesson_manifest["pipeline_status"] == "completed"
        assert lesson_manifest["video_url"] == "https://cdn.example.com/v.mp4"
        assert lesson_manifest["artifacts"] == {
            "script": "s3://bucket/script.txt",
            "slides": "s3://bucket/slides.pdf",
        }

    async def test_manifest_with_null_module_fields(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        """Module with description=None and summary=None should produce manifest without crashing."""
        course = Course(
            course_id="course-001",
            tenant_id="test-tenant",
            status="planning",
            source_config={"source_type": "web"},
            config={},
        )
        module = Module(
            module_id="mod-null",
            course_id="course-001",
            position=1,
            title="Module No Description",
            description=None,
            summary=None,
            intro_video_url=None,
            slide_deck_path=None,
        )
        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = [module]
        mock_repository.get_lessons.return_value = []

        manifest = await assembler.build_manifest("course-001", tenant_id="t1")

        mod_manifest = manifest["modules"][0]
        assert mod_manifest["module_id"] == "mod-null"
        assert mod_manifest["title"] == "Module No Description"
        assert mod_manifest["description"] is None
        assert mod_manifest["intro_video_url"] is None
        assert mod_manifest["slide_deck_path"] is None


class TestManifestLargeCourse:
    """Test manifest structure with many modules and lessons."""

    async def test_manifest_large_course(
        self,
        assembler: BundleAssembler,
        mock_repository: AsyncMock,
    ) -> None:
        """3 modules with 5 lessons each: correct counts, grouping, and ordering."""
        course = Course(
            course_id="course-big",
            tenant_id="test-tenant",
            status="completed",
            title="Large Course",
            description="A course with many modules and lessons",
            source_config={"source_type": "web"},
            config={},
        )

        modules = [
            Module(
                module_id=f"mod-{i:03d}",
                course_id="course-big",
                position=i,
                title=f"Module {i}",
                description=f"Description for module {i}",
            )
            for i in range(1, 4)
        ]

        lessons = []
        for mod_idx in range(1, 4):
            for les_idx in range(1, 6):
                lessons.append(
                    Lesson(
                        lesson_id=f"lesson-m{mod_idx}-l{les_idx}",
                        module_id=f"mod-{mod_idx:03d}",
                        course_id="course-big",
                        position=les_idx,
                        title=f"Module {mod_idx} Lesson {les_idx}",
                        topic=f"Topic {mod_idx}.{les_idx}",
                        target_duration_seconds=120,
                        pipeline_status="completed" if les_idx <= 3 else "pending",
                        video_url=(
                            f"https://cdn.example.com/m{mod_idx}-l{les_idx}.mp4"
                            if les_idx <= 3
                            else None
                        ),
                        artifacts=(
                            {"script": f"s3://bucket/m{mod_idx}-l{les_idx}.txt"}
                            if les_idx <= 3
                            else None
                        ),
                    )
                )

        mock_repository.get_course.return_value = course
        mock_repository.get_modules.return_value = modules
        mock_repository.get_lessons.return_value = lessons

        manifest = await assembler.build_manifest("course-big", tenant_id="t1")

        # Verify top-level counts
        assert manifest["course_id"] == "course-big"
        assert manifest["title"] == "Large Course"
        assert manifest["total_modules"] == 3
        assert manifest["total_lessons"] == 15

        # Verify module count
        assert len(manifest["modules"]) == 3

        # Verify each module has exactly 5 lessons in correct order
        for mod_idx, mod_manifest in enumerate(manifest["modules"], start=1):
            assert mod_manifest["module_id"] == f"mod-{mod_idx:03d}"
            assert mod_manifest["title"] == f"Module {mod_idx}"
            assert mod_manifest["position"] == mod_idx
            assert len(mod_manifest["lessons"]) == 5

            # Verify lessons are sorted by position
            for les_idx, les_manifest in enumerate(mod_manifest["lessons"], start=1):
                assert les_manifest["lesson_id"] == f"lesson-m{mod_idx}-l{les_idx}"
                assert les_manifest["position"] == les_idx
                assert les_manifest["title"] == f"Module {mod_idx} Lesson {les_idx}"
                assert les_manifest["topic"] == f"Topic {mod_idx}.{les_idx}"

            # Verify first 3 lessons have video_url and artifacts, last 2 do not
            for les_manifest in mod_manifest["lessons"][:3]:
                assert les_manifest["video_url"] is not None
                assert les_manifest["artifacts"] is not None
                assert les_manifest["pipeline_status"] == "completed"
            for les_manifest in mod_manifest["lessons"][3:]:
                assert les_manifest["video_url"] is None
                assert les_manifest["artifacts"] is None
                assert les_manifest["pipeline_status"] == "pending"

        # Verify manifest is JSON-serializable
        json_str = BundleAssembler.manifest_to_json(manifest)
        parsed = json.loads(json_str)
        assert parsed["total_lessons"] == 15
        assert len(parsed["modules"]) == 3
