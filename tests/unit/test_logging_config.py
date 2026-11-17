"""Tests for logging_config.py."""

from __future__ import annotations

import logging

from w2v_common.observability.request_context import (
    clear_request_id,
    set_request_id,
)

from src.logging_config import (
    ContextFormatter,
    JSONFormatter,
    configure_logging,
    course_id_var,
    generate_request_id,
    tenant_id_var,
)


class TestGenerateRequestId:
    def test_returns_12_char_hex(self) -> None:
        rid = generate_request_id()
        assert len(rid) == 12
        int(rid, 16)  # Should not raise

    def test_unique_ids(self) -> None:
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestJSONFormatter:
    def test_formats_basic_record(self) -> None:
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        import json

        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["message"] == "hello"
        assert data["logger"] == "test"

    def test_includes_request_id_when_set(self) -> None:
        formatter = JSONFormatter()
        set_request_id("req-123")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            import json

            assert json.loads(output)["request_id"] == "req-123"
        finally:
            clear_request_id()

    def test_includes_tenant_id_when_set(self) -> None:
        formatter = JSONFormatter()
        token = tenant_id_var.set("tenant-abc")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            import json

            assert json.loads(output)["tenant_id"] == "tenant-abc"
        finally:
            tenant_id_var.reset(token)

    def test_includes_course_id_when_set(self) -> None:
        formatter = JSONFormatter()
        token = course_id_var.set("course-xyz")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            import json

            assert json.loads(output)["course_id"] == "course-xyz"
        finally:
            course_id_var.reset(token)

    def test_excludes_empty_context_vars(self) -> None:
        formatter = JSONFormatter()
        clear_request_id()
        t2 = tenant_id_var.set("")
        t3 = course_id_var.set("")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="msg",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            import json

            data = json.loads(output)
            assert "request_id" not in data
            assert "tenant_id" not in data
            assert "course_id" not in data
        finally:
            clear_request_id()
            tenant_id_var.reset(t2)
            course_id_var.reset(t3)

    def test_includes_exception_info(self) -> None:
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="",
                lineno=0,
                msg="error occurred",
                args=(),
                exc_info=exc_info,
            )
            output = formatter.format(record)
            import json

            data = json.loads(output)
            assert "exception" in data
            assert "ValueError" in data["exception"]


class TestContextFormatter:
    def test_formats_with_all_context(self) -> None:
        formatter = ContextFormatter(
            fmt="%(ctx)s%(message)s",
        )
        set_request_id("req-1")
        t2 = tenant_id_var.set("t-1")
        t3 = course_id_var.set("c-1")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            assert "[req=req-1 tenant=t-1 course=c-1]" in output
            assert "hello" in output
        finally:
            clear_request_id()
            tenant_id_var.reset(t2)
            course_id_var.reset(t3)

    def test_formats_without_context(self) -> None:
        formatter = ContextFormatter(fmt="%(ctx)s%(message)s")
        clear_request_id()
        t2 = tenant_id_var.set("")
        t3 = course_id_var.set("")
        try:
            record = logging.LogRecord(
                name="test",
                level=logging.INFO,
                pathname="",
                lineno=0,
                msg="hello",
                args=(),
                exc_info=None,
            )
            output = formatter.format(record)
            assert output == "hello"
        finally:
            clear_request_id()
            tenant_id_var.reset(t2)
            course_id_var.reset(t3)


class TestConfigureLogging:
    def test_json_mode(self) -> None:
        configure_logging(json_logs=True, level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG
        assert len(root.handlers) > 0
        assert isinstance(root.handlers[-1].formatter, JSONFormatter)

    def test_text_mode(self) -> None:
        configure_logging(json_logs=False, level="WARNING")
        root = logging.getLogger()
        assert root.level == logging.WARNING
        assert isinstance(root.handlers[-1].formatter, ContextFormatter)

    def test_invalid_level_defaults_to_info(self) -> None:
        configure_logging(json_logs=False, level="NONEXISTENT")
        root = logging.getLogger()
        assert root.level == logging.INFO
