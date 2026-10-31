"""Live integration tests (DESIGN §7): READ-ONLY against a real Metabase instance.

Opt-in (auto-skip otherwise):

    MBCLI_INTEGRATION=1 MB_URL=http://host:3000 \\
        MB_USERNAME=… MB_PASSWORD=… uv run pytest -m integration

API-key mode is also supported (MB_URL + MB_API_KEY alone is enough).
Credentials come exclusively from the environment and are never written to any
file; the session cache is redirected to a per-run tmp dir, so the developer's
real ~/.cache/mbcli/session.json is never touched.

READ-ONLY DISCIPLINE (hard rule): the only executed SQL is `SELECT 1` and
`SELECT … LIMIT n` (n <= 100); everything else is metadata/GET endpoints. The
single multi-statement case asserts that the write-guard REFUSES it — the guard
runs before any config/network work, and the test proves zero server execution
by pointing MB_URL at an unreachable address with no credentials at all.
`--allow-write` is never used.

Invocation note: tests call the REAL entry point `mbcli.cli:main` (with
per-invocation env overrides) instead of `typer.testing.CliRunner`, because
CliRunner drives the app object directly and bypasses main()'s
MbcliError -> stderr-JSON / exit-code translation — error-path cases would
silently lose their diagnostics.
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from mbcli.cli import main

# Snapshot the integration environment at import time — conftest's autouse
# `isolated_env` fixture wipes every MB_* variable for each test afterwards.
_ENABLED = os.environ.get("MBCLI_INTEGRATION") == "1"
_URL = os.environ.get("MB_URL")
_API_KEY = os.environ.get("MB_API_KEY")
_USERNAME = os.environ.get("MB_USERNAME")
_PASSWORD = os.environ.get("MB_PASSWORD")
_TIMEOUT = os.environ.get("MB_TIMEOUT")

# One session cache per pytest run, shared by all cases => a single login for
# the whole module (the 401/corrupt-cache case intentionally re-logins once).
_CACHE_DIR = Path(tempfile.mkdtemp(prefix="mbcli-integration-"))
_CACHE_FILE = str(_CACHE_DIR / "session.json")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_ENABLED and _URL),
        reason="integration tests are opt-in: set MBCLI_INTEGRATION=1 and MB_URL "
        "(plus MB_USERNAME/MB_PASSWORD or MB_API_KEY); see DESIGN §7",
    ),
]


# -- runner ---------------------------------------------------------------------------


def _base_env(auth: bool = True) -> dict[str, str]:
    env = {"MB_URL": _URL, "MB_SESSION_CACHE": _CACHE_FILE}
    if _TIMEOUT:
        env["MB_TIMEOUT"] = _TIMEOUT
    if auth:
        if _API_KEY:
            env["MB_API_KEY"] = _API_KEY
        if _USERNAME:
            env["MB_USERNAME"] = _USERNAME
        if _PASSWORD:
            env["MB_PASSWORD"] = _PASSWORD
    return env


@dataclass
class LiveResult:
    exit_code: int
    stdout: str
    stderr: str

    def assert_exit(self, code: int) -> LiveResult:
        assert self.exit_code == code, (
            f"expected exit {code}, got {self.exit_code}\n"
            f"--- stdout ---\n{self.stdout[:2000]}\n--- stderr ---\n{self.stderr[:2000]}"
        )
        return self

    def json(self) -> Any:
        assert self.stdout.strip(), f"empty stdout; stderr: {self.stderr[:1000]!r}"
        return json.loads(self.stdout)

    def error(self) -> dict[str, Any]:
        lines = [line for line in self.stderr.strip().splitlines() if line.strip()]
        assert lines, f"no stderr JSON emitted; stdout: {self.stdout[:1000]!r}"
        parsed = json.loads(lines[-1])
        assert set(parsed) == {"error"}, f"expected single error object, got: {parsed}"
        return parsed["error"]


def run(*args: object, env: dict[str, str | None] | None = None) -> LiveResult:
    """Invoke the real CLI entry point with per-invocation env overrides.

    A None value in `env` REMOVES that variable for the invocation (used to
    prove health needs no credentials, and that the write guard fires before
    any config/network work).
    """
    overrides = _base_env()
    for key, value in (env or {}).items():
        if value is None:
            overrides.pop(key, None)
        else:
            overrides[key] = value
    saved = {key: os.environ.get(key) for key in overrides}
    stdout, stderr = io.StringIO(), io.StringIO()
    code = 0
    try:
        os.environ.update(overrides)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                main([str(arg) for arg in args])
            except SystemExit as exc:
                code = int(exc.code or 0)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return LiveResult(code, stdout.getvalue(), stderr.getvalue())


# -- 1-2: connection & identity ----------------------------------------------------------


def test_health_without_any_credentials():
    """health needs no credentials (lazy credential check, DESIGN §3.1)."""
    result = run(
        "health",
        env={"MB_USERNAME": None, "MB_PASSWORD": None, "MB_API_KEY": None},
    )
    result.assert_exit(0)
    payload = result.json()
    assert payload["status"] == "ok", f"unexpected health body: {payload}"
    assert payload["version"], f"version must be non-null: {payload}"


def test_whoami_shape():
    result = run("whoami").assert_exit(0)
    who = result.json()
    assert list(who) == ["id", "email", "is_superuser", "group_ids"], who
    assert isinstance(who["id"], int), who
    assert who["email"], who
    assert isinstance(who["group_ids"], list), who


# -- 3-6: metadata ------------------------------------------------------------------------


def test_databases_list_at_least_20_including_quant_data():
    result = run("databases", "list").assert_exit(0)
    databases = result.json()
    assert isinstance(databases, list) and len(databases) >= 20, (
        f"expected >= 20 databases (do NOT pin to an exact count), got {len(databases)}"
    )
    names = {db["name"] for db in databases}
    assert "quant_data" in names, f"quant_data missing from: {sorted(names)[:30]}…"
    assert list(databases[0]) == [
        "id",
        "name",
        "engine",
        "description",
        "is_sample",
        "initial_sync_status",
        "timezone",
        "dbms_version",
    ]


def test_tables_quant_data_300_plus_app_mau():
    result = run("tables", "--db", "quant_data").assert_exit(0)
    tables = result.json()
    assert isinstance(tables, list) and len(tables) >= 300, (
        f"quant_data should expose >= 300 tables, got {len(tables)}"
    )
    assert any(t["name"] == "app_mau" for t in tables), "app_mau missing from quant_data"
    assert list(tables[0]) == [
        "id",
        "schema",
        "name",
        "display_name",
        "description",
        "estimated_row_count",
        "active",
        "field_count",
    ], tables[0]


def test_schemas_quant_data_empty_schema_dominates():
    result = run("schemas", "--db", "quant_data").assert_exit(0)
    schemas = result.json()
    assert isinstance(schemas, list) and schemas, schemas
    assert list(schemas[0]) == ["name", "table_count"], schemas[0]
    empty = next((s for s in schemas if s["name"] == ""), None)
    assert empty is not None, f"MySQL default schema '' missing: {schemas}"
    assert empty["table_count"] >= 300, empty


def test_fields_app_mau_shape():
    result = run("fields", "--table", "app_mau", "--db", "quant_data").assert_exit(0)
    fields = result.json()
    assert isinstance(fields, list) and fields, "app_mau must have fields"
    assert list(fields[0]) == [
        "id",
        "name",
        "display_name",
        "base_type",
        "semantic_type",
        "database_type",
        "description",
        "is_pk",
        "is_nullable",
        "is_indexed",
        "fk_target",
        "position",
    ], fields[0]
    assert any(f["name"] == "date" for f in fields), "date column missing from app_mau"


# -- 7-10: query (read-only discipline!) ----------------------------------------------------


def test_query_select_1_envelope():
    result = run("query", "--db", "15", "--native", "SELECT 1 AS one").assert_exit(0)
    envelope = result.json()
    assert list(envelope) == [
        "status",
        "row_count",
        "rows_returned",
        "truncated",
        "columns",
        "rows",
        "running_time_ms",
        "cached",
        "data_warnings",
    ], envelope
    assert envelope["status"] == "completed", envelope
    assert envelope["row_count"] == 1 and envelope["rows_returned"] == 1, envelope
    assert envelope["truncated"] is False, envelope
    assert envelope["rows"] == [[1]], envelope
    assert envelope["columns"][0]["name"] == "one", envelope
    assert list(envelope["columns"][0]) == ["name", "base_type", "database_type"]


def test_query_limit_3_csv():
    result = run(
        "query",
        "--db",
        "quant_data",
        "--native",
        "SELECT * FROM app_mau LIMIT 3",
        "--format",
        "csv",
    ).assert_exit(0)
    rows = list(csv.reader(result.stdout.splitlines()))
    assert len(rows) == 4, f"expected header + 3 data rows, got {len(rows)}: {rows}"
    header, data = rows[0], rows[1:]
    assert "date" in header, header
    assert all(len(row) == len(header) and any(cell != "" for cell in row) for row in data)


def test_query_bad_table_is_bad_query():
    result = run(
        "query",
        "--db",
        "15",
        "--native",
        "SELECT * FROM mbcli_no_such_table_xyz LIMIT 1",
    )
    result.assert_exit(1)
    error = result.error()
    assert error["code"] == "bad_query", error
    assert error["detail"]["http_status"] == 400, error


def test_write_guard_refuses_before_any_server_contact():
    """`SELECT 1; DELETE …` is REFUSED (exit 2) with zero server execution: the
    guard fires before config/network, so even an unreachable URL + no
    credentials still yields write_blocked (never config_missing/network_error)."""
    result = run(
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 1; DELETE FROM app_mau",
        env={
            "MB_URL": "http://127.0.0.1:9",  # unreachable: any request would fail loudly
            "MB_USERNAME": None,
            "MB_PASSWORD": None,
            "MB_API_KEY": None,
        },
    )
    result.assert_exit(2)
    error = result.error()
    assert error["code"] == "write_blocked", error
    assert "--allow-write" in json.dumps(error, ensure_ascii=False), error


# -- 11-14: assets & context -----------------------------------------------------------------


def test_search_limit_3_whitelist():
    result = run("search", "数据", "--limit", "3").assert_exit(0)
    items = result.json()
    assert isinstance(items, list) and items, "search 数据 should match at least one item"
    assert len(items) <= 3, items
    keys = ["id", "model", "name", "description", "collection", "database_id", "updated_at"]
    assert all(list(item) == keys for item in items), items[0]


def test_cards_show_7876_native_sql():
    result = run("cards", "show", "7876").assert_exit(0)
    card = result.json()
    assert list(card) == [
        "id",
        "name",
        "description",
        "collection",
        "display",
        "query_type",
        "database_id",
        "sql",
        "mbql",
        "parameters",
        "created_at",
        "updated_at",
        "last_edit",
    ], card
    assert card["query_type"] == "native", card
    assert card["sql"] and "select" in card["sql"].lower(), card["sql"]


def test_collections_show_root_envelope():
    result = run("collections", "show", "root", "--model", "card", "--limit", "2").assert_exit(0)
    listing = result.json()
    assert list(listing) == ["total", "limit", "offset", "items"], listing
    assert listing["limit"] == 2 and listing["offset"] == 0, listing
    assert isinstance(listing["total"], int) and listing["total"] >= 0, listing
    assert isinstance(listing["items"], list) and len(listing["items"]) <= 2, listing


def test_context_markdown_for_app_mau():
    result = run("context", "--db", "15", "--table", "app_mau").assert_exit(0)
    markdown = result.stdout
    first_line = markdown.splitlines()[0] if markdown else ""
    assert first_line.startswith("# Database: quant_data"), first_line
    assert "## Table `app_mau`" in markdown, markdown[:500]
    assert "¹ estimated_row_count 为 Metabase 同步估值，仅作参考" in markdown
    assert "| column | type | semantic | pk | fk → | description |" in markdown


# -- 15: session cache resilience -------------------------------------------------------------


@pytest.mark.skipif(bool(_API_KEY), reason="api-key mode never reads the session cache")
def test_corrupt_session_cache_silently_relogins():
    # prime the cache with one real login
    run("whoami").assert_exit(0)
    assert Path(_CACHE_FILE).is_file(), "session cache should have been written"
    # corrupt it: unparseable content must be silently ignored, not an error
    Path(_CACHE_FILE).write_text("{not valid json", encoding="utf-8")
    result = run("whoami").assert_exit(0)  # transparent re-login
    assert result.json()["email"], result.stdout
    # and the cache is rewritten with a fresh, parseable session
    cached = json.loads(Path(_CACHE_FILE).read_text())
    assert "session_id" in cached, cached
