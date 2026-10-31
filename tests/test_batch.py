"""batch command tests (DESIGN §13.3) — offline, respx canned payloads.

Every batch line is replayed in-process through mbcli.cli:main, so per-line
mocks look exactly like standalone-invocation mocks. stdin is injected by
monkeypatching sys.stdin (the conftest `cli` fixture drives the real entry
point in-process).
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import httpx
import pytest

BASE = "http://test.local"

# -- canned payloads --------------------------------------------------------------


def _meta_table(table_id: int, name: str, fields: int = 0) -> dict[str, Any]:
    return {
        "id": table_id,
        "schema": "",
        "name": name,
        "display_name": name,
        "description": None,
        "estimated_row_count": 10,
        "active": True,
        "visibility_type": None,
        "fields": [{"id": i, "name": f"c{i}"} for i in range(fields)],
    }


DATABASES_PAYLOAD = {
    "data": [{"id": 7, "name": "bi_data", "engine": "mysql"}],
    "total": 1,
}

METADATA_PAYLOAD = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "tables": [_meta_table(3258, "futures_positions", 2), _meta_table(3259, "dim_carrier", 1)],
}

SEARCH_PAYLOAD = {
    "data": [
        {
            "id": 7876,
            "model": "card",
            "name": "手机卡",
            "description": None,
            "collection": {"id": 1039, "name": "报表"},
            "database_id": None,
            "updated_at": "2026-10-01T08:00:00.000Z",
        }
    ],
    "total": 1,
    "limit": 2,
    "offset": 0,
}

WHOAMI_PAYLOAD = {"id": 42, "email": "me@example.com", "is_superuser": False, "group_ids": [1]}


def _stdin(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def _env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Connection via env only: root options of the OUTER invocation are NOT
    inherited by batch lines (documented §13.3 caveat, pinned by a test)."""
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_API_KEY", "k")
    for key, value in overrides.items():
        monkeypatch.setenv(key, value)


def _mock_common(mock_api) -> None:
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    mock_api.get(f"{BASE}/api/database/15/metadata").mock(
        return_value=httpx.Response(200, json=METADATA_PAYLOAD)
    )
    mock_api.get(f"{BASE}/api/search").mock(return_value=httpx.Response(200, json=SEARCH_PAYLOAD))


def _lines(result) -> list[dict[str, Any]]:
    return [json.loads(line) for line in result.stdout.strip().splitlines()]


# -- golden NDJSON -------------------------------------------------------------------


def test_batch_golden_ndjson_three_commands(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(
        monkeypatch,
        "# 注释行\ndatabases list\n\ntables --db 15 --match fut\n   \nsearch 手机 --limit 2\n",
    )
    result = cli("batch").assert_ok(0)
    entries = _lines(result)
    assert len(entries) == 3  # comments/blanks skipped
    for entry in entries:
        assert list(entry) == ["index", "argv", "exit", "error", "result"]
    assert [e["index"] for e in entries] == [1, 2, 3]  # executed commands only
    assert [e["argv"] for e in entries] == [
        ["databases", "list"],
        ["tables", "--db", "15", "--match", "fut"],
        ["search", "手机", "--limit", "2"],
    ]
    assert all(e["exit"] == 0 and e["error"] is None for e in entries)
    assert entries[0]["result"] == [
        {
            "id": 7,
            "name": "bi_data",
            "engine": "mysql",
            "description": None,
            "is_sample": False,
            "initial_sync_status": None,
            "timezone": None,
            "dbms_version": None,
        }
    ]
    assert entries[1]["result"] == [
        {
            "id": 3258,
            "schema": "",
            "name": "futures_positions",
            "display_name": "futures_positions",
            "description": None,
            "estimated_row_count": 10,
            "active": True,
            "field_count": 2,
        }
    ]
    assert entries[2]["result"] == [
        {
            "id": 7876,
            "model": "card",
            "name": "手机卡",
            "description": None,
            "collection": {"id": 1039, "name": "报表"},
            "database_id": None,
            "database_name": None,  # P2-1: no db referenced → no index fetch
            "updated_at": "2026-10-01T08:00:00.000Z",
        }
    ]


def test_batch_failing_middle_line_isolated_and_batch_exit_1(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    mock_api.get(f"{BASE}/api/database/999/metadata").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    _stdin(
        monkeypatch,
        "databases list\ntables --db 999 --match x\ndatabases list\n",
    )
    result = cli("batch")
    assert result.exit_code == 1
    entries = _lines(result)
    assert len(entries) == 3  # the failing line never aborts the batch
    assert [e["exit"] for e in entries] == [0, 1, 0]
    assert entries[1]["error"]["code"] == "not_found"
    assert entries[2]["result"][0]["name"] == "bi_data"


def test_batch_error_matches_standalone_stderr(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    mock_api.get(f"{BASE}/api/database/999/metadata").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    _stdin(monkeypatch, "tables --db 999 --match x\n")
    result = cli("batch")
    entry = _lines(result)[0]
    direct = cli("--url", BASE, "--api-key", "k", "tables", "--db", "999", "--match", "x")
    assert entry["exit"] == direct.exit_code == 1
    assert entry["error"] == direct.error()  # identical §3.3 stderr contract


# -- line handling -------------------------------------------------------------------


def test_batch_nested_batch_rejected_but_continues(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "batch\ndatabases list\nbatch search x\n")
    result = cli("batch")
    assert result.exit_code == 1
    entries = _lines(result)
    assert [e["exit"] for e in entries] == [2, 0, 2]
    for entry in (entries[0], entries[2]):
        assert entry["error"]["code"] == "invalid_args"
        assert "nested" in entry["error"]["message"]
    assert entries[1]["result"][0]["name"] == "bi_data"


def test_batch_write_guard_line_exit_2_zero_network(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    dataset = mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(200, json={"status": "completed"})
    )
    _stdin(monkeypatch, 'query --db 15 --native "DELETE FROM t"\ndatabases list\n')
    result = cli("batch")
    entries = _lines(result)
    assert entries[0]["exit"] == 2
    assert entries[0]["error"]["code"] == "write_blocked"
    assert "--allow-write" in entries[0]["error"]["message"]
    assert dataset.call_count == 0  # the guard runs before any network work
    assert entries[1]["exit"] == 0


def test_batch_empty_stdin_exit_0_no_output(cli, monkeypatch):
    _stdin(monkeypatch, "")
    result = cli("batch").assert_ok(0)
    assert result.stdout == ""


def test_batch_only_comments_and_blanks_exit_0(cli, monkeypatch):
    _stdin(monkeypatch, "# a\n\n   \n# b\n")
    result = cli("batch").assert_ok(0)
    assert result.stdout == ""


def test_batch_usage_error_line_exit_2(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "search\ndatabases list\n")
    result = cli("batch")
    entries = _lines(result)
    assert entries[0]["exit"] == 2
    assert entries[0]["error"]["code"] == "invalid_args"
    assert entries[0]["result"] is None
    assert entries[1]["exit"] == 0


def test_batch_unknown_command_line_exit_2(cli, mock_api, monkeypatch):
    _stdin(monkeypatch, "frobnicate\n")
    result = cli("batch")
    assert result.exit_code == 1
    entry = _lines(result)[0]
    assert entry["exit"] == 2
    assert entry["error"]["code"] == "invalid_args"
    assert "No such command" in entry["error"]["message"]


def test_batch_quoted_argv(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "search '手机 卡' --limit 1\n")
    result = cli("batch").assert_ok(0)
    entry = _lines(result)[0]
    assert entry["argv"] == ["search", "手机 卡", "--limit", "1"]
    assert entry["exit"] == 0


def test_batch_unbalanced_quote_line_is_invalid_args(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "search 'abc\ndatabases list\n")
    result = cli("batch")
    assert result.exit_code == 1
    entries = _lines(result)
    assert entries[0]["exit"] == 2
    assert entries[0]["error"]["code"] == "invalid_args"
    assert "shlex" in entries[0]["error"]["message"]
    assert entries[1]["exit"] == 0  # later lines still run


# -- result shaping -------------------------------------------------------------------


def test_batch_non_json_result_is_raw_string(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "databases list --format csv\n")
    result = cli("batch").assert_ok(0)
    entry = _lines(result)[0]
    assert isinstance(entry["result"], str)
    assert entry["result"].startswith("id,name,engine")


def test_batch_out_line_result_is_summary_object(cli, mock_api, monkeypatch, tmp_path):
    _env(monkeypatch)
    _mock_common(mock_api)
    target = tmp_path / "out.json"
    _stdin(monkeypatch, f"databases list --out {target}\n")
    result = cli("batch").assert_ok(0)
    entry = _lines(result)[0]
    assert entry["result"] == {"out": str(target), "rows": 1, "truncated": False}
    assert target.exists()


# -- shared session / in-process semantics ----------------------------------------------


def test_batch_shared_session_login_once_across_lines(cli, mock_api, monkeypatch):
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "me@example.com")
    monkeypatch.setenv("MB_PASSWORD", "secret")
    login = mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "s-123"})
    )
    mock_api.get(f"{BASE}/api/user/current").mock(
        return_value=httpx.Response(200, json=WHOAMI_PAYLOAD)
    )
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    _stdin(monkeypatch, "whoami\ndatabases list\nwhoami\n")
    result = cli("batch").assert_ok(0)
    entries = _lines(result)
    assert [e["exit"] for e in entries] == [0, 0, 0]
    # one login on the first authed line; the disk cache serves the other two
    assert login.call_count == 1
    assert entries[0]["result"] == {
        "id": 42,
        "email": "me@example.com",
        "is_superuser": False,
        "group_ids": [1],
    }


def test_batch_root_options_not_inherited_from_outer(cli, mock_api, monkeypatch):
    # outer invocation carries --url/--api-key, the line does not and no env is
    # set: the line must fail config_missing (per-line CliState, §13.3 caution)
    _stdin(monkeypatch, "databases list\n")
    result = cli("--url", BASE, "--api-key", "k", "batch")
    assert result.exit_code == 1
    entry = _lines(result)[0]
    assert entry["exit"] == 1
    assert entry["error"]["code"] == "config_missing"


def test_batch_index_counts_only_executed_lines(cli, mock_api, monkeypatch):
    _env(monkeypatch)
    _mock_common(mock_api)
    _stdin(monkeypatch, "# c\ndatabases list\n\n# c2\ndatabases list\n")
    result = cli("batch").assert_ok(0)
    entries = _lines(result)
    assert [e["index"] for e in entries] == [1, 2]  # comments/blanks never indexed
    assert [e["exit"] for e in entries] == [0, 0]


def test_batch_line_may_carry_own_root_options(cli, mock_api, monkeypatch):
    _mock_common(mock_api)
    _stdin(monkeypatch, f"--url {BASE} --api-key k databases list\n")
    result = cli("batch").assert_ok(0)
    entry = _lines(result)[0]
    assert entry["exit"] == 0
    assert entry["result"][0]["name"] == "bi_data"
