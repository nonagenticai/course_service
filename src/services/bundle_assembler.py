"""Assemble course manifest and bundle for download."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any

from w2v_common.config import StorageSettings

from src.database.models import Lesson, Module
from src.database.repository import CourseRepository

logger = logging.getLogger(__name__)


class BundleAssembler:
    """Build course manifest and upload bundle to object storage."""

    def __init__(
        self,
        repository: CourseRepository,
        storage_settings: StorageSettings | None = None,
    ) -> None:
        self._repo = repository
        self._storage = storage_settings

    async def build_manifest(self, course_id: str, *, tenant_id: str) -> dict[str, Any]:
        """Build a course manifest JSON from database state.

        Returns:
            Manifest dict with course metadata, modules, and lesson video URLs.
        """
        course = await self._repo.get_course(course_id, tenant_id=tenant_id)
        if not course:
            raise ValueError(f"Course {course_id} not found")

        modules = await self._repo.get_modules(course_id, tenant_id=tenant_id)
        lessons = await self._repo.get_lessons(course_id, tenant_id=tenant_id)

        lessons_by_module: dict[str, list[Lesson]] = {}
        for ln in lessons:
            mid = str(ln.module_id)
            lessons_by_module.setdefault(mid, []).append(ln)

        manifest = {
            "course_id": course.course_id,
            "title": course.title,
            "description": course.description,
            "total_modules": len(modules),
            "total_lessons": len(lessons),
            "modules": [
                self._build_module_manifest(mod, lessons_by_module.get(str(mod.module_id), []))
                for mod in modules
            ],
        }

        # Persist manifest to course record
        await self._repo.update_course(course_id, tenant_id=tenant_id, manifest=manifest)

        logger.info(
            "BundleAssembler: manifest built for course %s — %d modules, %d lessons",
            course_id,
            len(modules),
            len(lessons),
        )
        return manifest

    def _build_module_manifest(self, module: Module, lessons: list[Lesson]) -> dict[str, Any]:
        """Build manifest entry for a single module."""
        sorted_lessons = sorted(lessons, key=lambda ln: ln.position)
        return {
            "module_id": module.module_id,
            "title": module.title,
            "description": module.description,
            "position": module.position,
            "intro_video_url": module.intro_video_url,
            "slide_deck_path": module.slide_deck_path,
            "lessons": [
                {
                    "lesson_id": ln.lesson_id,
                    "title": ln.title,
                    "topic": ln.topic,
                    "position": ln.position,
                    "video_url": ln.video_url,
                    "target_duration_seconds": ln.target_duration_seconds,
                    "pipeline_status": ln.pipeline_status,
                    "artifacts": ln.artifacts,
                }
                for ln in sorted_lessons
            ],
        }

    async def upload_bundle(
        self, course_id: str, manifest: dict[str, Any], *, tenant_id: str
    ) -> str | None:
        """Upload a course manifest bundle to MinIO and return a presigned download URL.

        Falls back gracefully if MinIO is not configured -- logs a warning and returns None.

        Args:
            course_id: The course identifier.
            manifest: The manifest dict to serialize and upload.

        Returns:
            Presigned download URL (valid 7 days) or None if storage is unavailable.
        """
        if (
            not self._storage
            or not self._storage.minio_access_key
            or not self._storage.minio_secret_key
        ):
            logger.warning(
                "MinIO not configured -- skipping bundle upload for course %s", course_id
            )
            return None

        try:
            from miniopy_async import Minio

            client = Minio(
                self._storage.minio_endpoint,
                access_key=self._storage.minio_access_key,
                secret_key=self._storage.minio_secret_key,
                secure=self._storage.minio_secure,
            )

            bucket = self._storage.minio_bucket
            # Ensure bucket exists
            if not await client.bucket_exists(bucket):
                await client.make_bucket(bucket)

            # Serialize and upload
            import io

            data = json.dumps(manifest, indent=2, default=str).encode("utf-8")
            object_name = f"{course_id}/manifest.json"
            stream = io.BytesIO(data)

            await client.put_object(
                bucket,
                object_name,
                stream,
                length=len(data),
                content_type="application/json",
            )

            # Generate presigned URL valid for 7 days
            url = await client.presigned_get_object(bucket, object_name, expires=timedelta(days=7))

            logger.info("Bundle uploaded for course %s: %s", course_id, object_name)

            # Persist bundle_url to course record
            await self._repo.update_course(course_id, tenant_id=tenant_id, bundle_url=url)

            return url

        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning(
                "Failed to upload bundle for course %s: %s", course_id, exc, exc_info=True
            )
            return None

    @staticmethod
    def manifest_to_json(manifest: dict[str, Any]) -> str:
        """Serialize manifest to formatted JSON string."""
        return json.dumps(manifest, indent=2, default=str)
