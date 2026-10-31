"""Command tests for `query`, `cards run`, `cards show` (DESIGN §4, §5.2, §5.3; M2).

Golden tests with canned respx payloads modeled on the REAL probe shapes
(DESIGN §0): status/data{cols,rows}/row_count/running_time/cached, where cols
entries carry lib/* and other upstream keys that must NOT leak into output.
Offline only (respx); no test touches the network.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

BASE = "http://test.local"

DATABASES_PAYLOAD = {
    "data": [
        {"id": 7, "name": "bi_data", "engine": "mysql"},
        {"id": 15, "name": "quant_data", "engine": "mysql"},
        {"id": 21, "name": "dup", "engine": "postgres"},
        {"id": 22, "name": "dup", "engine": "sqlite"},
    ],
    "total": 4,
}

# Modeled on the live probe of `SELECT 1`: cols carry many upstream keys
# (display_name / effective_type / lib/* / field_ref / fingerprint ...) that the
# ColumnInfo whitelist must drop.
SELECT_ONE_COLS = [
    {
        "name": "one",
        "display_name": "one",
        "base_type": "type/BigInteger",
        "effective_type": "type/BigInteger",
        "database_type": "BIGINT",
        "lib/type": "type/BigInteger",
        "lib/source": "breakout",
        "field_ref": ["field", "one", {"base-type": "type/BigInteger"}],
        "id": None,
        "quoted": False,
        "fingerprint": None,
        "remapped_to": None,
        "remapped_from": None,
        "nested": False,
    },
    {
        "name": "s",
        "display_name": "s",
        "base_type": "type/Text",
        "effective_type": "type/Text",
        "database_type": "VARCHAR",
        "lib/type": "type/Text",
        "lib/source": "native",
        "field_ref": ["field", "s", {"base-type": "type/Text"}],
        "id": 37677,
        "quoted": False,
        "fingerprint": {"global": {"distinct-count": 3}},
        "semantic_type": "type/Category",
    },
]

ENVELOPE_COLUMNS = [
    {"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"},
    {"name": "s", "base_type": "type/Text", "database_type": "VARCHAR"},
]

ENVELOPE_KEYS = [
    "status",
    "row_count",
    "rows_returned",
    "truncated",
    "columns",
    "rows",
    "running_time_ms",
    "cached",
    "data_warnings",
]


def dataset_payload(
    rows: list[list[Any]],
    *,
    cols: list[dict[str, Any]] | None = None,
    rows_truncated: bool = False,
    running_time: int = 88,
    cached: bool = False,
) -> dict[str, Any]:
    """Build a /api/dataset response in the probe shape (DESIGN §0)."""
    return {
        "status": "completed",
        "data": {
            "cols": SELECT_ONE_COLS if cols is None else cols,
            "rows": rows,
            "rows_truncated": rows_truncated,
            "insights": None,
            "apply_auto_limit": False,
            "native_form": {"query": "SELECT …", "template-tags": {}},
        },
        "row_count": len(rows),
        "running_time": running_time,
        "cached": cached,
        "context": "question",
        "error": None,
    }


def mock_dataset(mock_api: Any, payload: dict[str, Any]) -> Any:
    return mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(200, json=payload))


def mock_card_query(mock_api: Any, card_id: int, payload: dict[str, Any]) -> Any:
    return mock_api.post(f"{BASE}/api/card/{card_id}/query").mock(
        return_value=httpx.Response(200, json=payload)
    )


def mock_databases(mock_api: Any) -> Any:
    return mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )


def sent_body(route: Any) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)


def run_query(cli: Any, mock_api: Any, *extra: object) -> Any:
    """Standard happy-path invocation: 2x2 result from the canned payload."""
    mock_dataset(mock_api, dataset_payload([[1, "x"], [2, "y"]]))
    return cli("--url", BASE, "--api-key", "k", "query", "--db", "15", *extra)


# == query: envelope golden (§5.2) ==============================================


def test_query_json_envelope_golden(cli, mock_api):
    payload = dataset_payload([[1, "x"]], running_time=123, cached=True)
    mock_dataset(mock_api, payload)
    result = cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1"
    ).assert_ok(0)
    assert result.json() == {
        "status": "completed",
        "row_count": 1,
        "rows_returned": 1,
        "truncated": False,
        "columns": ENVELOPE_COLUMNS,
        "rows": [[1, "x"]],
        "running_time_ms": 123,
        "cached": True,
        "data_warnings": [],
    }


def test_query_envelope_key_order_is_the_contract(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1").assert_ok(0)
    assert list(result.json()) == ENVELOPE_KEYS


def test_query_column_whitelist_drops_upstream_keys(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1").assert_ok(0)
    columns = result.json()["columns"]
    assert columns == ENVELOPE_COLUMNS
    assert all(list(col) == ["name", "base_type", "database_type"] for col in columns)
    for leaked in ("lib/type", "lib/source", "field_ref", "display_name", "fingerprint"):
        assert leaked not in result.stdout


def test_query_post_body_exact_shape(cli, mock_api):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1 AS one"
    ).assert_ok(0)
    assert sent_body(route) == {
        "database": 15,
        "type": "native",
        "native": {"query": "SELECT 1 AS one", "template-tags": {}},
        "parameters": [],
    }


def test_query_numeric_db_id_never_lists_databases(cli, mock_api):
    listing = mock_databases(mock_api)
    run_query(cli, mock_api, "--native", "SELECT 1").assert_ok(0)
    assert listing.call_count == 0


def test_query_db_name_is_resolved_to_id(cli, mock_api):
    listing = mock_databases(mock_api)
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "quant_data", "--native", "SELECT 1"
    ).assert_ok(0)
    assert listing.call_count == 1
    assert sent_body(route)["database"] == 15


def test_query_unknown_db_name_is_not_found(cli, mock_api):
    mock_databases(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "ghost", "--native", "SELECT 1")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "not_found"
    assert error["detail"]["ref"] == "ghost"


def test_query_ambiguous_db_name_exit_1(cli, mock_api):
    mock_databases(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "dup", "--native", "SELECT 1")
    assert result.exit_code == 1
    assert result.error()["code"] == "ambiguous_ref"


# == query: client-side truncation (ADR #12) ====================================


def test_query_truncation_boundary_1001_rows_default_1000(cli, mock_api):
    payload = dataset_payload([[i] for i in range(1001)], cols=[SELECT_ONE_COLS[0]])
    mock_dataset(mock_api, payload)
    result = cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT id"
    ).assert_ok(0)
    parsed = result.json()
    assert parsed["truncated"] is True
    assert parsed["rows_returned"] == 1000
    assert parsed["row_count"] == 1001
    assert len(parsed["rows"]) == 1000
    assert parsed["rows"][0] == [0] and parsed["rows"][-1] == [999]


def test_query_max_rows_explicit(cli, mock_api):
    payload = dataset_payload([[i] for i in range(8)], cols=[SELECT_ONE_COLS[0]])
    mock_dataset(mock_api, payload)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 8",
        "--max-rows",
        "5",
    ).assert_ok(0)
    parsed = result.json()
    assert parsed["truncated"] is True
    assert parsed["rows_returned"] == 5
    assert parsed["row_count"] == 8
    assert parsed["rows"] == [[0], [1], [2], [3], [4]]


def test_query_max_rows_exact_boundary_not_truncated(cli, mock_api):
    payload = dataset_payload([[i] for i in range(10)], cols=[SELECT_ONE_COLS[0]])
    mock_dataset(mock_api, payload)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 10",
        "--max-rows",
        "10",
    ).assert_ok(0)
    parsed = result.json()
    assert parsed["truncated"] is False
    assert parsed["rows_returned"] == 10
    assert parsed["row_count"] == 10


def test_query_max_rows_zero_means_unlimited(cli, mock_api):
    payload = dataset_payload([[i] for i in range(1205)], cols=[SELECT_ONE_COLS[0]])
    mock_dataset(mock_api, payload)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT many",
        "--max-rows",
        "0",
    ).assert_ok(0)
    parsed = result.json()
    assert parsed["truncated"] is False
    assert parsed["rows_returned"] == 1205
    assert parsed["row_count"] == 1205
    assert len(parsed["rows"]) == 1205


def test_query_negative_max_rows_is_usage_error(cli):
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 1",
        "--max-rows",
        "-1",
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_query_server_rows_truncated_becomes_data_warning(cli, mock_api):
    payload = dataset_payload([[1, "x"]], rows_truncated=True)
    mock_dataset(mock_api, payload)
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1")
    result.assert_ok(0)
    parsed = result.json()
    # server-side truncation is a warning, NOT the client truncated flag
    assert parsed["truncated"] is False
    assert parsed["rows_returned"] == 1
    assert len(parsed["data_warnings"]) == 1
    assert "rows_truncated" in parsed["data_warnings"][0]


# == query: formats (§3.6 / §5.2) ===============================================


def test_query_ndjson_exact_lines(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1", "--format", "ndjson").assert_ok(0)
    lines = result.stdout.splitlines()
    assert len(lines) == 4
    assert json.loads(lines[0]) == {"columns": ENVELOPE_COLUMNS}
    assert json.loads(lines[1]) == [1, "x"]
    assert json.loads(lines[2]) == [2, "y"]
    assert json.loads(lines[3]) == {"truncated": False, "rows_returned": 2}


def test_query_ndjson_truncated_sentinel(cli, mock_api):
    payload = dataset_payload([[1], [2], [3]], cols=[SELECT_ONE_COLS[0]])
    mock_dataset(mock_api, payload)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 3",
        "--format",
        "ndjson",
        "--max-rows",
        "2",
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert len(lines) == 4  # envelope line + 2 rows + sentinel
    assert json.loads(lines[-2]) == [2]
    assert json.loads(lines[-1]) == {"truncated": True, "rows_returned": 2}


def test_query_csv_header_and_rows(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1", "--format", "csv").assert_ok(0)
    assert result.stdout == "one,s\r\n1,x\r\n2,y\r\n"


def test_query_csv_rfc4180_escaping_and_unicode(cli, mock_api):
    payload = dataset_payload(
        [["a,b"], ['say "hi"'], ["line\nbreak"], ["上海"], [None]],
        cols=[SELECT_ONE_COLS[1]],
    )
    mock_dataset(mock_api, payload)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT s",
        "--format",
        "csv",
    ).assert_ok(0)
    assert result.stdout == ('s\r\n"a,b"\r\n"say ""hi"""\r\n"line\nbreak"\r\n上海\r\n""\r\n')


def test_query_table_format(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1", "--format", "table").assert_ok(0)
    assert "one" in result.stdout and "s" in result.stdout
    assert "x" in result.stdout and "y" in result.stdout


def test_query_out_file_json_envelope_and_stdout_summary(cli, mock_api, tmp_path):
    target = tmp_path / "r.json"
    result = run_query(cli, mock_api, "--native", "SELECT 1", "--out", str(target)).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 2, "truncated": False}
    written = json.loads(target.read_text())
    assert written["rows_returned"] == 2
    assert written["rows"] == [[1, "x"], [2, "y"]]


def test_query_out_file_csv(cli, mock_api, tmp_path):
    target = tmp_path / "r.csv"
    result = run_query(
        cli, mock_api, "--native", "SELECT 1", "--format", "csv", "--out", str(target)
    ).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 2, "truncated": False}
    # read bytes: read_text() would normalize the RFC 4180 CRLF line endings
    assert target.read_bytes().decode() == "one,s\r\n1,x\r\n2,y\r\n"


def test_query_fields_is_rejected_on_envelope(cli, mock_api):
    result = run_query(cli, mock_api, "--native", "SELECT 1", "--fields", "rows")
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert "envelope" in error["message"]


def test_query_missing_native_is_usage_error(cli):
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


# == query: write-guard integration (§3.4) ======================================


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM t",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a=1",
        "DROP TABLE t",
        "TRUNCATE TABLE t",
        "ALTER TABLE t ADD c INT",
    ],
)
def test_query_write_statements_blocked_exit_2_without_http(cli, mock_api, sql):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", sql)
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "write_blocked"
    assert "--allow-write" in error["message"]
    assert route.call_count == 0


def test_query_multi_statement_injection_blocked(cli, mock_api):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 1; DELETE FROM t",
    )
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "write_blocked"
    assert error["detail"]["keyword"] == "DELETE"
    assert route.call_count == 0


def test_query_comment_disguised_delete_blocked(cli):
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "-- harmless\nDELETE FROM t",
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "write_blocked"


def test_query_allow_write_bypasses_guard(cli, mock_api):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "DELETE FROM t",
        "--allow-write",
    ).assert_ok(0)
    assert route.call_count == 1
    assert sent_body(route)["native"]["query"] == "DELETE FROM t"


def test_query_cte_is_allowed(cli, mock_api):
    sql = "WITH cte AS (SELECT 1 AS one) SELECT one FROM cte"
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", sql).assert_ok(0)
    assert sent_body(route)["native"]["query"] == sql


@pytest.mark.parametrize("sql", ["SHOW TABLES", "DESCRIBE t", "EXPLAIN SELECT 1"])
def test_query_other_readonly_keywords_allowed(cli, mock_api, sql):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", sql).assert_ok(0)
    assert sent_body(route)["native"]["query"] == sql


def test_query_guard_runs_before_config_validation(cli):
    """A write statement is rejected (exit 2) even with no URL configured at all."""
    result = cli("query", "--db", "15", "--native", "DELETE FROM t")
    assert result.exit_code == 2
    assert result.error()["code"] == "write_blocked"


def test_query_empty_sql_is_invalid_args(cli):
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_query_comment_only_sql_is_invalid_args(cli):
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "-- only")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


# == query: --param serialization (§4, M2: variable targets only) ===============


def test_query_params_serialize_into_body(cli, mock_api):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT {{dt}}",
        "--param",
        "dt=2024-01-01",
        "--param",
        "city=上海",
    ).assert_ok(0)
    body = sent_body(route)
    # parameters: id (stable uuid, v0.2) + type/target/value per DESIGN §4
    assert [p["type"] for p in body["parameters"]] == ["category", "category"]
    for param, key, value in zip(
        body["parameters"], ("dt", "city"), ("2024-01-01", "上海"), strict=True
    ):
        assert set(param) == {"id", "type", "target", "value"}
        assert uuid.UUID(param["id"])  # server-accepted shape: id must be present
        assert param["target"] == ["variable", ["template-tag", key]]
        assert param["value"] == value
    # template-tags: every referenced variable must be declared (v0.2 live fix)
    tags = body["native"]["template-tags"]
    assert set(tags) == {"dt", "city"}
    assert tags["dt"]["type"] == "date"  # 2024-01-01 → ISO date
    assert tags["city"]["type"] == "text"  # 上海 → text
    for key, tag in tags.items():
        assert set(tag) == {"id", "name", "display-name", "type", "required"}
        assert tag["name"] == key and tag["display-name"] == key
        assert tag["required"] is False
        uuid.UUID(tag["id"])


def test_query_param_value_may_contain_equals(cli, mock_api):
    route = mock_dataset(mock_api, dataset_payload([[1, "x"]]))
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT {{k}}",
        "--param",
        "k=a=b",
    ).assert_ok(0)
    body = sent_body(route)
    assert [p["value"] for p in body["parameters"]] == ["a=b"]
    assert body["native"]["template-tags"]["k"]["type"] == "text"


@pytest.mark.parametrize("bad", ["noequals", "=v", ""])
def test_query_malformed_param_is_invalid_args(cli, mock_api, bad):
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "15",
        "--native",
        "SELECT 1",
        "--param",
        bad,
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


# == query: error propagation ===================================================


def test_query_dataset_400_status_failed_is_bad_query(cli, mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "Table 'quant_data.nope' doesn't exist",
                "status": "failed",
                "error_type": "invalid-query",
                "class": "class metabase.driver...SQLException",
            },
        )
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT * FROM nope"
    )
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "bad_query"
    assert error["message"] == "Table 'quant_data.nope' doesn't exist"
    assert error["detail"]["http_status"] == 400
    assert error["detail"]["error_type"] == "invalid-query"


def test_query_400_field_validation_is_invalid_request(cli, mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400, json={"errors": {"database": "value must be an integer greater than 0"}}
        )
    )
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "invalid_request"
    assert error["detail"]["errors"] == {"database": "value must be an integer greater than 0"}


def test_query_500_is_server_error(cli, mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(500, text="down"))
    result = cli("--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1")
    assert result.exit_code == 1
    assert result.error()["code"] == "server_error"


# == cards run ==================================================================


def test_cards_run_post_body_shape(cli, mock_api):
    route = mock_card_query(mock_api, 7876, dataset_payload([[1, "x"]]))
    cli("--url", BASE, "--api-key", "k", "cards", "run", "7876").assert_ok(0)
    assert sent_body(route) == {"parameters": [], "ignore_cache": False}


def test_cards_run_params_in_body(cli, mock_api):
    route = mock_card_query(mock_api, 7876, dataset_payload([[1, "x"]]))
    # card 7876 declares no parameters → fallback spec (fresh uuid + category)
    mock_api.get(f"{BASE}/api/card/7876").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7876,
                "name": "数据日期",
                "parameters": [],
                "dataset_query": {
                    "database": 15,
                    "type": "native",
                    "native": {"query": "select 1", "template-tags": {}},
                },
            },
        )
    )
    cli("--url", BASE, "--api-key", "k", "cards", "run", "7876", "--param", "city=上海").assert_ok(
        0
    )
    body = sent_body(route)
    assert body["ignore_cache"] is False
    assert len(body["parameters"]) == 1
    param = body["parameters"][0]
    assert set(param) == {"id", "type", "target", "value"}
    assert uuid.UUID(param["id"])
    assert param["type"] == "category"
    assert param["target"] == ["variable", ["template-tag", "city"]]
    assert param["value"] == "上海"


def test_cards_run_envelope_golden(cli, mock_api):
    payload = dataset_payload([[65, "2025-10-01"]], running_time=45)
    mock_card_query(mock_api, 7876, payload)
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "7876").assert_ok(0)
    assert result.json() == {
        "status": "completed",
        "row_count": 1,
        "rows_returned": 1,
        "truncated": False,
        "columns": ENVELOPE_COLUMNS,
        "rows": [[65, "2025-10-01"]],
        "running_time_ms": 45,
        "cached": False,
        "data_warnings": [],
    }


def test_cards_run_truncation(cli, mock_api):
    payload = dataset_payload([[i] for i in range(1001)], cols=[SELECT_ONE_COLS[0]])
    mock_card_query(mock_api, 7876, payload)
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "7876").assert_ok(0)
    parsed = result.json()
    assert parsed["truncated"] is True
    assert parsed["rows_returned"] == 1000
    assert parsed["row_count"] == 1001


def test_cards_run_csv(cli, mock_api):
    mock_card_query(mock_api, 7876, dataset_payload([[1, "x"], [2, "y"]]))
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "run", "7876", "--format", "csv"
    ).assert_ok(0)
    assert result.stdout == "one,s\r\n1,x\r\n2,y\r\n"


def test_cards_run_ndjson_sentinel(cli, mock_api):
    mock_card_query(mock_api, 7876, dataset_payload([[1, "x"]]))
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "run", "7876", "--format", "ndjson"
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert json.loads(lines[-2]) == [1, "x"]
    assert json.loads(lines[-1]) == {"truncated": False, "rows_returned": 1}


def test_cards_run_out_file(cli, mock_api, tmp_path):
    target = tmp_path / "card.json"
    mock_card_query(mock_api, 7876, dataset_payload([[1, "x"], [2, "y"]]))
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "run", "7876", "--out", str(target)
    ).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 2, "truncated": False}
    assert json.loads(target.read_text())["rows_returned"] == 2


def test_cards_run_fields_rejected(cli, mock_api):
    mock_card_query(mock_api, 7876, dataset_payload([[1, "x"]]))
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "7876", "--fields", "rows")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_cards_run_archived_card_is_bad_query(cli, mock_api):
    mock_api.post(f"{BASE}/api/card/1234/query").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "Card is archived",
                "status": "failed",
                "error_type": "invalid-query",
            },
        )
    )
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "1234")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "bad_query"
    assert error["detail"]["error_type"] == "invalid-query"


def test_cards_run_non_integer_id_is_usage_error(cli):
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "seven")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


# == cards show (§5.3) ==========================================================

CARD_NATIVE = {
    "id": 7876,
    "name": "数据日期",
    "description": "latest data date per table",
    "collection_id": 1039,
    "collection": {"id": 1039, "name": "量化日报", "authority_level": None},
    "display": "scalar",
    "dataset_query": {
        "database": 15,
        "type": "native",
        "native": {"query": "SELECT MAX(date) AS d FROM app_mau", "template-tags": {}},
        "parameters": [],
    },
    "parameters": [
        {"id": "p1", "name": "城市", "slug": "city", "type": "category", "default": None}
    ],
    "created_at": "2024-05-01T02:03:04.567Z",
    "updated_at": "2025-10-01T02:03:04.567Z",
    "last-edit-info": {
        "id": 5,
        "first_name": "Zhang",
        "last_name": "San",
        "email": "zhang@example.com",
        "timestamp": "2025-10-01T02:03:04.567Z",
    },
    "archived": False,
    "enable_embedding": False,
    "embedding_params": None,
    "view_count": 12,
    "creator": {"id": 5, "email": "zhang@example.com", "common_name": "dropped"},
    "unknown_future_key": "dropped",
}

CARD_NATIVE_EXPECTED = {
    "id": 7876,
    "name": "数据日期",
    "description": "latest data date per table",
    "collection": {"id": 1039, "name": "量化日报"},
    "display": "scalar",
    "query_type": "native",
    "database_id": 15,
    "sql": "SELECT MAX(date) AS d FROM app_mau",
    "mbql": None,
    "parameters": [
        {"id": "p1", "name": "城市", "slug": "city", "type": "category", "default": None}
    ],
    "created_at": "2024-05-01T02:03:04.567Z",
    "updated_at": "2025-10-01T02:03:04.567Z",
    "last_edit": {"user": "Zhang San", "at": "2025-10-01T02:03:04.567Z"},
}


def mock_card(mock_api: Any, card: dict[str, Any]) -> Any:
    return mock_api.get(f"{BASE}/api/card/{card['id']}").mock(
        return_value=httpx.Response(200, json=card)
    )


def test_cards_show_native_card_golden(cli, mock_api):
    mock_card(mock_api, CARD_NATIVE)
    result = cli("--url", BASE, "--api-key", "k", "cards", "show", "7876").assert_ok(0)
    parsed = result.json()
    assert parsed == CARD_NATIVE_EXPECTED
    assert list(parsed) == [
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
    ]
    for leaked in ("unknown_future_key", "archived", "view_count", "creator", "collection_id"):
        assert leaked not in result.stdout


def test_cards_show_mbql_card_golden(cli, mock_api):
    mbql_query = {
        "source-table": 3258,
        "aggregation": [["count"]],
        "breakout": [["field", 37677, None]],
        "filter": [">", ["field", 37678, None], "2025-01-01"],
    }
    card = {
        "id": 9,
        "name": "MAU trend",
        "description": None,
        "collection_id": None,
        "display": "line",
        "dataset_query": {"database": 15, "type": "query", "query": mbql_query},
        "parameters": [],
        "created_at": "2024-05-01T00:00:00.000Z",
        "updated_at": "2025-10-01T00:00:00.000Z",
    }
    mock_card(mock_api, card)
    result = cli("--url", BASE, "--api-key", "k", "cards", "show", "9").assert_ok(0)
    parsed = result.json()
    assert parsed["query_type"] == "mbql"
    assert parsed["sql"] is None
    assert parsed["mbql"] == mbql_query
    assert parsed["collection"] is None
    assert parsed["last_edit"] is None
    assert parsed["database_id"] == 15


def test_cards_show_fields_prunes_keys(cli, mock_api):
    mock_card(mock_api, CARD_NATIVE)
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "show", "7876", "--fields", "id,sql"
    ).assert_ok(0)
    assert result.json() == {"id": 7876, "sql": "SELECT MAX(date) AS d FROM app_mau"}


def test_cards_show_unknown_field_exit_2(cli, mock_api):
    mock_card(mock_api, CARD_NATIVE)
    result = cli("--url", BASE, "--api-key", "k", "cards", "show", "7876", "--fields", "id,oops")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_cards_show_csv_unsupported(cli, mock_api):
    mock_card(mock_api, CARD_NATIVE)
    result = cli("--url", BASE, "--api-key", "k", "cards", "show", "7876", "--format", "csv")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_cards_show_table_format(cli, mock_api):
    mock_card(mock_api, CARD_NATIVE)
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "show", "7876", "--format", "table"
    ).assert_ok(0)
    assert "数据日期" in result.stdout
    assert "SELECT MAX(date) AS d FROM app_mau" in result.stdout


def test_cards_show_out_file(cli, mock_api, tmp_path):
    target = tmp_path / "card.json"
    mock_card(mock_api, CARD_NATIVE)
    result = cli(
        "--url", BASE, "--api-key", "k", "cards", "show", "7876", "--out", str(target)
    ).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 1, "truncated": False}
    assert json.loads(target.read_text())["id"] == 7876


def test_cards_show_404_is_not_found(cli, mock_api):
    mock_api.get(f"{BASE}/api/card/999999").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    result = cli("--url", BASE, "--api-key", "k", "cards", "show", "999999")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"
