"""Tests for config.py — Settings, validation, and helper methods."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import (
    CourseSettings,
    Settings,
    clear_settings_cache,
    get_settings,
)


class TestPeerDiscoveryRemoved:
    """Peer URL config has been removed in favor of w2v_common.discovery.service_url."""

    def test_no_downstream_settings_attribute(self):
        """Settings must NOT expose a downstream sub-config anymore."""
        s = Settings.model_construct()
        assert not hasattr(s, "downstream"), (
            "Peer URLs must come from w2v_common.discovery.service_url, "
            "not from a Settings.downstream sub-config."
        )

    def test_downstream_settings_class_removed(self):
        """Symbol DownstreamSettings must no longer be importable from src.config."""
        import src.config as cfg

        assert not hasattr(cfg, "DownstreamSettings"), (
            "DownstreamSettings was removed in favor of w2v_common.discovery.service_url."
        )


class TestServiceDiscoveryWiring:
    """Verify the canonical peer URLs the service consumes."""

    def test_orchestrator_url_from_discovery(self, monkeypatch):
        monkeypatch.setenv("W2V_ORCHESTRATOR_URL", "http://orch.test:1234")
        from w2v_common.discovery import service_url

        assert service_url("orchestrator") == "http://orch.test:1234"

    def test_analysis_url_from_discovery(self, monkeypatch):
        monkeypatch.setenv("W2V_ANALYSIS_URL", "http://analysis.test:1234")
        from w2v_common.discovery import service_url

        assert service_url("analysis") == "http://analysis.test:1234"

    def test_slide_url_from_discovery(self, monkeypatch):
        monkeypatch.setenv("W2V_SLIDE_SERVICE_URL", "http://slide.test:1234")
        from w2v_common.discovery import service_url

        assert service_url("slide_service") == "http://slide.test:1234"

    def test_default_urls_use_k8s_dns(self, monkeypatch):
        """Without overrides, peer URLs resolve to in-cluster k8s DNS names."""
        monkeypatch.delenv("W2V_LOCAL", raising=False)
        for var in ("W2V_ORCHESTRATOR_URL", "W2V_ANALYSIS_URL", "W2V_SLIDE_SERVICE_URL"):
            monkeypatch.delenv(var, raising=False)
        from w2v_common.discovery import service_url

        # Assert the wiring, not the deployment suffix: the library's DNS
        # convention is its own concern, this repo only needs the right peer
        # name and port to come back.
        assert service_url("orchestrator").startswith("http://w2v-orchestrator.")
        assert service_url("orchestrator").endswith(":6300")
        assert service_url("analysis").startswith("http://analysis-service.")
        assert service_url("analysis").endswith(":6303")
        assert service_url("slide_service").startswith("http://slide-service.")
        assert service_url("slide_service").endswith(":6312")


class TestCourseSettings:
    def test_defaults(self):
        # LLM provider config (model/base_url/api_key) was removed when
        # course_service migrated to the WM gateway — provider routing now
        # lives in worker_management.
        cs = CourseSettings.model_validate({})
        assert cs.default_concurrency_limit == 10
        assert cs.default_max_retries == 3
        assert cs.default_max_lessons == 200
        assert cs.default_lesson_duration == 120
        assert cs.callback_base_url == ""

    def test_no_llm_provider_fields(self):
        """LLM provider fields must NOT be exposed — WM owns this."""
        cs = CourseSettings.model_validate({})
        for forbidden in ("llm_model", "llm_base_url", "llm_api_key"):
            assert not hasattr(cs, forbidden), (
                f"CourseSettings should not expose {forbidden!r}; LLM calls "
                "go through worker_management (WMClient)."
            )

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("COURSE_DEFAULT_MAX_LESSONS", "50")
        cs = CourseSettings.model_validate({})
        assert cs.default_max_lessons == 50

    def test_extra_env_ignored(self, monkeypatch):
        """Stale COURSE_LLM_* env vars from old configmaps must not blow up."""
        monkeypatch.setenv("COURSE_LLM_MODEL", "left-over")
        monkeypatch.setenv("COURSE_LLM_BASE_URL", "https://stale")
        # extra="ignore" in model_config — silently drop unknown fields.
        cs = CourseSettings.model_validate({})
        assert cs.default_max_lessons == 200


class TestSettings:
    """Settings tests — must set API_PORT to avoid the port-8000 validator."""

    def test_defaults(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert s.app_name == "course_service"
        assert s.environment == "development"
        assert s.app_version == "0.1.0"

    def test_sub_configs_are_correct_types(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert "postgresql" in s.database.url
        assert isinstance(s.auth.enabled, bool)
        assert s.redis.url.startswith("redis://")
        assert s.api.port == 6314
        assert s.storage.backend in ("local", "minio")
        assert s.logging.level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        assert s.course.default_max_lessons > 0

    def test_get_database_url(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert "postgresql" in s.get_database_url()

    def test_get_redis_url(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert "redis" in s.get_redis_url()

    def test_is_production(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        monkeypatch.setenv("ENVIRONMENT", "production")
        s = Settings()
        assert s.is_production() is True
        assert s.is_development() is False

    def test_is_development(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert s.is_development() is True
        assert s.is_production() is False

    def test_port_8000_rejected(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "8000")
        with pytest.raises(ValidationError, match="8000"):
            Settings()

    def test_port_6314_accepted(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        s = Settings()
        assert s.api.port == 6314


class TestGetSettings:
    def test_returns_settings_instance(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        clear_settings_cache()
        s = get_settings()
        assert isinstance(s, Settings)

    def test_cached(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        clear_settings_cache()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_clear_cache(self, monkeypatch):
        monkeypatch.setenv("API_PORT", "6314")
        clear_settings_cache()
        s1 = get_settings()
        clear_settings_cache()
        s2 = get_settings()
        assert s1 is not s2
