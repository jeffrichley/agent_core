"""Tests for agent_core.logging — structured JSON logging + correlation-id contextvar.

Covers:
- JsonFormatter outputs valid JSON with all five required fields.
- JsonFormatter includes exc_info key when record.exc_info is set.
- CorrelationIdFilter stamps record.correlation_id from active contextvar.
- CorrelationIdFilter stamps empty string when no id is bound.
- bind_correlation_id + Token.reset() restores previous value.
- configure_logging("json") installs JsonFormatter on root logger.
- configure_logging("pretty") installs a plain logging.Formatter (not JsonFormatter).
- CorrelationIdFilter is attached to the handler in both modes.
- End-to-end: a log emitted inside a _dispatch()-like wrapper appears in captured
  records with the correct correlation_id field when the JSON handler is active.
- LoggingConfig(format="unknown") raises pydantic.ValidationError.
- DaemonConfig without a logging: key yields logging.format == "pretty".
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from unittest import mock

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_core.logging import (
    CorrelationIdFilter,
    JsonFormatter,
    bind_correlation_id,
    configure_logging,
    correlation_id,
)

# ---------------------------------------------------------------------------
# Root-logger isolation fixture — required to prevent global logging state
# from bleeding across pytest-xdist workers sharing the same interpreter.
# ---------------------------------------------------------------------------


@pytest.fixture()
def restore_root_logger():
    """Save and restore root logger handlers + level around each test."""
    handlers = logging.root.handlers[:]
    level = logging.root.level
    yield
    logging.root.handlers[:] = handlers
    logging.root.setLevel(level)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    msg: str = "hello",
    name: str = "test.logger",
    level: int = logging.INFO,
    exc_info=None,
) -> logging.LogRecord:
    """Create a minimal LogRecord without going through the logging machinery."""
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname="",
        lineno=0,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    return record


# ---------------------------------------------------------------------------
# CorrelationIdFilter
# ---------------------------------------------------------------------------


def test_correlation_id_filter_stamps_from_contextvar():
    """CorrelationIdFilter copies the active contextvar value onto the record."""
    tok = bind_correlation_id("test-abc-123")
    try:
        filt = CorrelationIdFilter()
        record = _make_record()
        filt.filter(record)
        assert record.correlation_id == "test-abc-123"
    finally:
        correlation_id.reset(tok)


def test_correlation_id_filter_stamps_empty_string_when_unbound():
    """CorrelationIdFilter stamps empty string when no correlation id is bound."""
    # Ensure no id is set (reset to default)
    tok = bind_correlation_id("")
    try:
        filt = CorrelationIdFilter()
        record = _make_record()
        filt.filter(record)
        assert record.correlation_id == ""
    finally:
        correlation_id.reset(tok)


def test_correlation_id_filter_returns_true():
    """CorrelationIdFilter.filter() always returns True (passes the record through)."""
    filt = CorrelationIdFilter()
    record = _make_record()
    result = filt.filter(record)
    assert result is True


# ---------------------------------------------------------------------------
# bind_correlation_id + Token isolation
# ---------------------------------------------------------------------------


def test_bind_correlation_id_token_reset_restores_previous():
    """bind_correlation_id returns a Token; resetting it restores the previous value."""
    outer_tok = bind_correlation_id("outer")
    try:
        assert correlation_id.get() == "outer"
        inner_tok = bind_correlation_id("inner")
        assert correlation_id.get() == "inner"
        correlation_id.reset(inner_tok)
        assert correlation_id.get() == "outer"
    finally:
        correlation_id.reset(outer_tok)


def test_bind_correlation_id_isolation_across_handlings():
    """Simulates two sequential dispatch handlings; each sees only its own id."""
    seen_ids = []

    def _simulate_dispatch(corr_id: str) -> None:
        tok = bind_correlation_id(corr_id)
        try:
            seen_ids.append(correlation_id.get())
        finally:
            correlation_id.reset(tok)

    _simulate_dispatch("first-handling")
    _simulate_dispatch("second-handling")

    assert seen_ids == ["first-handling", "second-handling"]
    # After both handlings the contextvar should be back to default (empty string).
    assert correlation_id.get() == ""


# ---------------------------------------------------------------------------
# JsonFormatter
# ---------------------------------------------------------------------------


def test_json_formatter_outputs_valid_json():
    """JsonFormatter.format() returns a valid JSON string."""
    tok = bind_correlation_id("json-test-id")
    try:
        formatter = JsonFormatter()
        record = _make_record(msg="testing 1 2 3")
        record.correlation_id = "json-test-id"  # filter normally stamps this
        output = formatter.format(record)
        data = json.loads(output)
        assert isinstance(data, dict)
    finally:
        correlation_id.reset(tok)


def test_json_formatter_has_five_required_fields():
    """JsonFormatter output carries timestamp, level, logger, message, correlation_id."""
    formatter = JsonFormatter()
    record = _make_record(msg="field-check")
    record.correlation_id = "cid-xyz"
    output = formatter.format(record)
    data = json.loads(output)
    for field in ("timestamp", "level", "logger", "message", "correlation_id"):
        assert field in data, f"missing field: {field}"
    assert data["message"] == "field-check"
    assert data["correlation_id"] == "cid-xyz"
    assert data["level"] == "INFO"
    assert data["logger"] == "test.logger"


def test_json_formatter_includes_exc_info_when_set():
    """JsonFormatter adds exc_info key when record.exc_info is truthy."""
    formatter = JsonFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()

    record = _make_record(msg="error here", exc_info=exc_info)
    record.correlation_id = ""
    output = formatter.format(record)
    data = json.loads(output)
    assert "exc_info" in data
    assert "ValueError" in data["exc_info"]
    assert "boom" in data["exc_info"]


def test_json_formatter_no_exc_info_key_when_not_set():
    """JsonFormatter does NOT include exc_info when record.exc_info is falsy."""
    formatter = JsonFormatter()
    record = _make_record()
    record.correlation_id = ""
    output = formatter.format(record)
    data = json.loads(output)
    assert "exc_info" not in data


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_json_installs_json_formatter(restore_root_logger):
    """configure_logging('json') installs JsonFormatter on the root logger."""
    configure_logging("json")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    handler = root.handlers[0]
    assert isinstance(handler.formatter, JsonFormatter)


def test_configure_logging_pretty_installs_plain_formatter(restore_root_logger):
    """configure_logging('pretty') installs a plain Formatter, not JsonFormatter."""
    configure_logging("pretty")
    root = logging.getLogger()
    assert len(root.handlers) >= 1
    handler = root.handlers[0]
    assert not isinstance(handler.formatter, JsonFormatter)
    assert isinstance(handler.formatter, logging.Formatter)


def test_configure_logging_json_attaches_correlation_filter(restore_root_logger):
    """configure_logging('json') attaches CorrelationIdFilter to the handler."""
    configure_logging("json")
    root = logging.getLogger()
    handler = root.handlers[0]
    filter_types = [type(f) for f in handler.filters]
    assert CorrelationIdFilter in filter_types


def test_configure_logging_pretty_attaches_correlation_filter(restore_root_logger):
    """configure_logging('pretty') also attaches CorrelationIdFilter to the handler."""
    configure_logging("pretty")
    root = logging.getLogger()
    handler = root.handlers[0]
    filter_types = [type(f) for f in handler.filters]
    assert CorrelationIdFilter in filter_types


def test_configure_logging_sets_root_level_to_info(restore_root_logger):
    """configure_logging sets root logger level to INFO."""
    logging.root.setLevel(logging.WARNING)  # set to something else first
    configure_logging("json")
    assert logging.root.level == logging.INFO


def test_configure_logging_clears_existing_handlers(restore_root_logger):
    """configure_logging replaces any pre-existing root handlers."""
    # Add two dummy handlers
    logging.root.addHandler(logging.NullHandler())
    logging.root.addHandler(logging.NullHandler())
    configure_logging("json")
    # Should end up with exactly one handler (the new StreamHandler)
    assert len(logging.root.handlers) == 1


# ---------------------------------------------------------------------------
# End-to-end: log emitted inside a _dispatch()-like wrapper
# ---------------------------------------------------------------------------


def test_end_to_end_dispatch_wrapper_correlation_id_in_json(restore_root_logger):
    """A log emitted inside a _dispatch()-like wrapper carries the correct correlation_id."""
    stream = io.StringIO()
    configure_logging("json")

    # Replace the root handler's stream with our StringIO to capture output
    root = logging.getLogger()
    assert len(root.handlers) == 1
    handler = root.handlers[0]
    handler.stream = stream

    test_logger = logging.getLogger("test.dispatch")

    corr_id = "e2e-dispatch-001"
    tok = bind_correlation_id(corr_id)
    try:
        test_logger.info("dispatching envelope")
    finally:
        correlation_id.reset(tok)

    output = stream.getvalue().strip()
    assert output, "no log output captured"
    data = json.loads(output)
    assert data["correlation_id"] == corr_id
    assert data["message"] == "dispatching envelope"


def test_correlation_id_not_present_after_reset(restore_root_logger):
    """After resetting the token, a subsequent log sees an empty correlation_id."""
    stream = io.StringIO()
    configure_logging("json")
    root = logging.getLogger()
    handler = root.handlers[0]
    handler.stream = stream

    test_logger = logging.getLogger("test.reset")

    # First dispatch — sets correlation_id
    tok = bind_correlation_id("first-id")
    try:
        test_logger.info("inside first")
    finally:
        correlation_id.reset(tok)

    # Second dispatch — no correlation_id set (should be empty string default)
    test_logger.info("outside dispatch")

    lines = [ln for ln in stream.getvalue().strip().splitlines() if ln]
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["correlation_id"] == "first-id"
    assert second["correlation_id"] == ""


# ---------------------------------------------------------------------------
# Config model tests (LoggingConfig + DaemonConfig)
# ---------------------------------------------------------------------------


def test_logging_config_unknown_format_raises_validation_error():
    """LoggingConfig(format='unknown') raises pydantic.ValidationError (not silent fallback)."""
    from agent_core.bus.config import LoggingConfig

    with pytest.raises(ValidationError):
        LoggingConfig(format="unknown")  # type: ignore[arg-type]


def test_logging_config_default_is_pretty():
    """LoggingConfig() defaults to format='pretty'."""
    from agent_core.bus.config import LoggingConfig

    cfg = LoggingConfig()
    assert cfg.format == "pretty"


def test_daemon_config_without_logging_key_defaults_to_pretty():
    """DaemonConfig instantiated without a logging: key yields logging.format == 'pretty'."""
    from agent_core.bus.config import DaemonConfig

    cfg = DaemonConfig()
    assert cfg.logging.format == "pretty"


def test_daemon_config_with_logging_json():
    """DaemonConfig with logging.format='json' is valid and sets the correct value."""
    from agent_core.bus.config import DaemonConfig

    cfg = DaemonConfig.model_validate({"logging": {"format": "json"}})
    assert cfg.logging.format == "json"


# ---------------------------------------------------------------------------
# CLI: bus run — logging config wiring (covers cli.py lines added in this spec)
# ---------------------------------------------------------------------------


@pytest.fixture()
def bus_config_file(tmp_path: Path) -> Path:
    """A minimal valid agent_core.yaml with no logging section."""
    config: dict = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [],
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))
    return p


def test_run_command_calls_configure_logging_pretty_by_default(
    bus_config_file: Path, restore_root_logger
):
    """bus run reads the YAML and calls configure_logging('pretty') when no logging section."""
    from agent_core.cli import app

    runner = CliRunner()
    with mock.patch("agent_core.bus.cli.asyncio.run", side_effect=lambda coro: coro.close()):
        runner.invoke(app, ["bus", "run", "--config", str(bus_config_file)])
    # configure_logging('pretty') sets root level to INFO and adds a non-JSON handler.
    assert logging.root.level == logging.INFO
    assert len(logging.root.handlers) >= 1
    assert not isinstance(logging.root.handlers[0].formatter, JsonFormatter)


def test_run_command_calls_configure_logging_json_when_configured(
    tmp_path: Path, restore_root_logger
):
    """bus run reads the YAML and calls configure_logging('json') when format=json."""
    from agent_core.cli import app

    config: dict = {
        "bus": {"storage_path": str(tmp_path / "bus.sqlite")},
        "endpoints": [],
        "logging": {"format": "json"},
    }
    p = tmp_path / "agent_core.yaml"
    p.write_text(yaml.dump(config))

    runner = CliRunner()
    with mock.patch("agent_core.bus.cli.asyncio.run", side_effect=lambda coro: coro.close()):
        runner.invoke(app, ["bus", "run", "--config", str(p)])
    # configure_logging('json') sets root level to INFO and adds a JsonFormatter handler.
    assert logging.root.level == logging.INFO
    assert len(logging.root.handlers) >= 1
    assert isinstance(logging.root.handlers[0].formatter, JsonFormatter)
