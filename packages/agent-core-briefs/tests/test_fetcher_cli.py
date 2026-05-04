from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from agent_core_briefs.fetchers.cli import CliFetcher


@pytest.mark.asyncio
async def test_text_capture():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {"command": ["python", "-c", "print('hello')"], "parse": "text"},
        datetime.now(UTC),
    )
    assert result["stdout"].strip() == "hello"


@pytest.mark.asyncio
async def test_json_parse():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {
            "command": ["python", "-c", "import json; print(json.dumps({'k': 'v'}))"],
            "parse": "json",
        },
        datetime.now(UTC),
    )
    assert result == {"k": "v"}


@pytest.mark.asyncio
async def test_lines_parse():
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {
            "command": ["python", "-c", "print('a'); print('b'); print('')"],
            "parse": "lines",
        },
        datetime.now(UTC),
    )
    assert result == {"lines": ["a", "b"]}


@pytest.mark.asyncio
async def test_nonzero_exit_raises_with_stderr():
    fetcher = CliFetcher()
    with pytest.raises(RuntimeError, match="exit"):
        await fetcher.fetch(
            {
                "command": [
                    "python",
                    "-c",
                    "import sys; print('err', file=sys.stderr); sys.exit(2)",
                ],
                "parse": "text",
            },
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_invalid_json_raises():
    fetcher = CliFetcher()
    with pytest.raises(ValueError, match="json"):
        await fetcher.fetch(
            {"command": ["python", "-c", "print('not json')"], "parse": "json"},
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_env_passthrough_only_listed_keys(monkeypatch):
    monkeypatch.setenv("PASSED_VAR", "yes")
    monkeypatch.setenv("BLOCKED_VAR", "no")
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {
            "command": [
                "python",
                "-c",
                "import os; print(os.environ.get('PASSED_VAR', 'X'), "
                "os.environ.get('BLOCKED_VAR', 'X'))",
            ],
            "parse": "text",
            "env_passthrough": ["PASSED_VAR"],
        },
        datetime.now(UTC),
    )
    assert "yes X" in result["stdout"]


@pytest.mark.asyncio
async def test_cwd_changes_working_directory(tmp_path):
    fetcher = CliFetcher()
    result = await fetcher.fetch(
        {
            "command": ["python", "-c", "import os; print(os.getcwd())"],
            "parse": "text",
            "cwd": str(tmp_path),
        },
        datetime.now(UTC),
    )
    # On Windows tmp_path may have a different case; compare resolved paths
    assert Path(result["stdout"].strip()).resolve() == tmp_path.resolve()


@pytest.mark.asyncio
async def test_json_list_root_raises():
    fetcher = CliFetcher()
    with pytest.raises(ValueError, match="json root must be"):
        await fetcher.fetch(
            {
                "command": ["python", "-c", "import json; print(json.dumps([1, 2, 3]))"],
                "parse": "json",
            },
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_yaml_list_root_raises():
    fetcher = CliFetcher()
    with pytest.raises(ValueError, match="yaml root must be"):
        await fetcher.fetch(
            {
                "command": ["python", "-c", "print('- a'); print('- b')"],
                "parse": "yaml",
            },
            datetime.now(UTC),
        )
