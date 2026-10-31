"""v0.2 wave 1 contract tests (DESIGN §13.4 + stubs of §13.1–13.3).

Covers everything this wave adds:
- markdown emitter + the five-format audit across ALL record-array commands
- search: --match-any merge/dedupe and --limit 0 fetch-all (page 100, cap 1000,
  truncation annotation per §5.2 philosophy)
- fields --search: cross-table column search, zero extra requests, reserved flag
- MySQL reserved-word list (pinned subset) + bad_query backtick hint in client
- -v request-log destination: stdout stays pure data, logs on stderr
- freshness / overview / batch stubs: full §13 signatures, not_implemented

All offline (respx); every fixture/helper is local to this file.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mbcli.output import Format, emit_records, render_markdown
from mbcli.reserved import MYSQL_RESERVED_WORDS, find_reserved_token, is_reserved

BASE = "http://test.local"

# -- shared canned payloads ------------------------------------------------------


def _search_item(item_id: int, model: str, name: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "model": model,
        "name": name,
        "description": None,
        "collection": {"id": 1039, "name": "报表"},
        "database_id": None,
        "updated_at": "2026-10-01T08:00:00.000Z",
    }


def _search_page(items: list[dict[str, Any]], total: int, limit: int, offset: int):
    return {
        "data": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "models": [],
        "available_models": ["card", "dashboard", "table"],
    }


DATABASES_PAYLOAD = {
    "data": [{"id": 15, "name": "quant_data", "engine": "mysql"}],
    "total": 1,
}

METADATA_PAYLOAD: dict[str, Any] = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "tables": [
        {
            "id": 3258,
            "schema": "",
            "name": "trade_events",
            "display_name": "trade_events",
            "description": None,
            "estimated_row_count": 10,
            "active": True,
            "visibility_type": None,
            "fields": [
                {
                    "id": 101,
                    "name": "year_month",
                    "display_name": "year_month",
                    "base_type": "type/Text",
                    "semantic_type": None,
                    "database_type": "VARCHAR",
                    "database_is_pk": False,
                    "database_position": 1,
                    "fk_target_field_id": None,
                },
                {
                    "id": 102,
                    "name": "event_key",
                    "display_name": "event_key",
                    "base_type": "type/BigInteger",
                    "semantic_type": "type/FK",
                    "database_type": "BIGINT",
                    "database_is_pk": False,
                    "database_position": 2,
                    "fk_target_field_id": 9001,
                },
            ],
        },
        {
            "id": 3259,
            "schema": "",
            "name": "dim_carrier",
            "display_name": "dim_carrier",
            "description": None,
            "estimated_row_count": 5,
            "active": True,
            "visibility_type": None,
            "fields": [
                {
                    "id": 9001,
                    "name": "id",
                    "base_type": "type/BigInteger",
                    "semantic_type": "type/PK",
                    "database_type": "BIGINT",
                    "database_is_pk": True,
                    "database_position": 1,
                },
                {
                    "id": 9002,
                    "name": "phone",
                    "base_type": "type/Text",
                    "semantic_type": "type/Phone",
                    "database_type": "VARCHAR",
                    "database_is_pk": False,
                    "database_position": 2,
                },
            ],
        },
        {
            "id": 3260,
            "schema": "",
            "name": "hidden_stats",
            "display_name": "hidden_stats",
            "description": None,
            "estimated_row_count": 1,
            "active": True,
            "visibility_type": "hidden",
            "fields": [
                {
                    "id": 700,
                    "name": "year_month",
                    "base_type": "type/Text",
                    "database_type": "VARCHAR",
                    "database_is_pk": False,
                },
                {
                    "id": 701,
                    "name": "key",
                    "base_type": "type/Text",
                    "database_type": "VARCHAR",
                    "database_is_pk": False,
                },
            ],
        },
    ],
}

QUERY_METADATA_PAYLOAD: dict[str, Any] = {
    "id": 3258,
    "name": "trade_events",
    "schema": "",
    "db_id": 15,
    "fields": [
        {
            "id": 101,
            "name": "year_month",
            "display_name": "year_month",
            "base_type": "type/Text",
            "semantic_type": None,
            "database_type": "VARCHAR",
            "description": None,
            "database_is_pk": False,
            "database_is_nullable": True,
            "database_indexed": False,
            "database_position": 1,
            "position": 99,
            "target": None,
            "fk_target_field_id": None,
        },
    ],
}

COLLECTIONS_PAYLOAD = [
    {"id": "root", "name": "Our analytics", "parent_id": None, "is_personal": False},
    {"id": 663, "name": "共享报表", "parent_id": None, "is_personal": False},
]

COLLECTION_ITEMS_PAYLOAD = {
    "total": 2,
    "data": [
        {
            "id": 7876,
            "model": "card",
            "name": "数据日期",
            "description": None,
            "collection": {"id": 1039, "name": "报表"},
            "display": "scalar",
            "updated_at": "2026-10-01T08:00:00.000Z",
        },
        {
            "id": 1607,
            "model": "dashboard",
            "name": "运营看板",
            "description": None,
            "collection": {"id": 1039, "name": "报表"},
            "display": None,
            "updated_at": "2026-10-02T08:00:00.000Z",
        },
    ],
    "models": ["card", "dashboard"],
    "limit": 20,
    "offset": 0,
}

WHOAMI_PAYLOAD = {"id": 42, "email": "me@example.com", "is_superuser": False, "group_ids": [1]}


def mock_get(mock_api, url: str, payload: Any):
    return mock_api.get(url).mock(return_value=httpx.Response(200, json=payload))


# -- markdown emitter -----------------------------------------------------------------


def test_render_markdown_pipe_table_unicode():
    out = render_markdown(["id", "名称"], [[7, "数据日期"], [15, "quant|数据"]])
    lines = out.splitlines()
    assert lines[0] == "| id | 名称 |"
    assert lines[1] == "|---|---|"
    assert lines[2] == "| 7 | 数据日期 |"
    assert lines[3] == "| 15 | quant\\|数据 |"  # pipe escaped inside cells


def test_render_markdown_none_and_newline_cells():
    out = render_markdown(["a", "b"], [[None, "x\ny"]])
    assert out.splitlines()[2] == "|  | x y |"


def test_emit_records_markdown(capsys):
    emit_records([{"id": 1, "name": "手机"}], fmt=Format.markdown)
    out = capsys.readouterr().out
    assert out.startswith("| id | name |\n|---|---|\n| 1 | 手机 |\n")


def test_emit_object_markdown(capsys):
    from mbcli.output import emit_object

    emit_object({"status": "ok", "version": "v0.66"}, fmt=Format.markdown)
    out = capsys.readouterr().out
    assert out.startswith("| key | value |\n|---|---|\n| status | ok |\n")


# -- §13.4 format audit: every record-array command x every format ----------------------

# id -> (argv, json_shape, first_header): json_shape distinguishes bare record
# arrays from the collections-show {total,limit,offset,items} envelope; the
# first_header pins the header row for csv/table/markdown.
RECORD_COMMANDS = {
    "databases-list": (("databases", "list"), "list", "id"),
    "schemas": (("schemas", "--db", "15"), "list", "name"),
    "tables": (("tables", "--db", "15"), "list", "id"),
    "fields-table": (("fields", "--table", "3258"), "list", "id"),
    "fields-search": (("fields", "--search", "year_month", "--db", "15"), "list", "table"),
    "search": (("search", "数据"), "list", "id"),
    "collections-list": (("collections", "list"), "list", "id"),
    "collections-show": (("collections", "show", "root"), "envelope", "id"),
}


def _mock_command_endpoints(mock):
    mock_get(mock, f"{BASE}/api/database", DATABASES_PAYLOAD)
    mock_get(mock, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    mock_get(mock, f"{BASE}/api/table/3258/query_metadata", QUERY_METADATA_PAYLOAD)
    mock_get(
        mock, f"{BASE}/api/search", _search_page([_search_item(1, "card", "数据日期")], 1, 50, 0)
    )
    mock_get(mock, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    mock_get(mock, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)


@pytest.mark.parametrize("fmt", ["json", "csv", "ndjson", "table", "markdown"])
@pytest.mark.parametrize("command", RECORD_COMMANDS.items(), ids=list(RECORD_COMMANDS))
def test_record_commands_accept_all_five_formats(cli, mock_api, command, fmt):
    _, (argv, json_shape, first_header) = command
    _mock_command_endpoints(mock_api)
    result = cli("--url", BASE, "--api-key", "k", *argv, "--format", fmt).assert_ok(0)
    out = result.stdout
    if fmt == "json":
        parsed = json.loads(out)
        if json_shape == "list":
            assert isinstance(parsed, list) and parsed
        else:  # collections show keeps its envelope in json (§3.6)
            assert isinstance(parsed, dict) and isinstance(parsed["items"], list)
    elif fmt == "csv":
        lines = out.splitlines()
        assert len(lines) >= 2 and lines[0].startswith(first_header + ",")
    elif fmt == "ndjson":
        records = [json.loads(line) for line in out.strip().splitlines()]
        assert records and all(isinstance(r, dict) for r in records)
    elif fmt == "table":
        assert first_header in out
    else:  # markdown
        assert out.startswith(f"| {first_header} ")
        assert out.splitlines()[1].startswith("|---")


def test_markdown_respects_fields_selection(cli, mock_api):
    _mock_command_endpoints(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "tables",
        "--db",
        "15",
        "--format",
        "markdown",
        "--fields",
        "name,id",
    ).assert_ok(0)
    assert result.stdout.startswith("| name | id |\n|---|---|\n| trade_events | 3258 |\n")


# -- search: --match-any ---------------------------------------------------------------


def _mock_search_by_query(mock, pools: dict[str, list[dict[str, Any]]], totals: dict[str, int]):
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        keyword = params["q"]
        offset = int(params.get("offset", 0))
        limit = int(params.get("limit", 50))
        items = pools.get(keyword, [])
        page = items[offset : offset + limit]
        return httpx.Response(
            200, json=_search_page(page, totals.get(keyword, len(items)), limit, offset)
        )

    return mock.get(f"{BASE}/api/search").mock(side_effect=handler)


def test_search_match_any_merges_and_dedupes(cli, mock_api):
    route = _mock_search_by_query(
        mock_api,
        {
            "手机": [_search_item(1, "card", "手机卡"), _search_item(2, "table", "手机表")],
            "mobile": [_search_item(2, "table", "手机表"), _search_item(3, "card", "mobile卡")],
        },
        {"手机": 2, "mobile": 2},
    )
    result = cli("--url", BASE, "--api-key", "k", "search", "手机,mobile", "--match-any").assert_ok(
        0
    )
    parsed = result.json()
    assert [(i["model"], i["id"]) for i in parsed] == [
        ("card", 1),
        ("table", 2),
        ("card", 3),
    ]  # first-seen order, (model,id) dedupe
    queries = [call.request.url.params.get("q") for call in route.calls]
    assert queries == ["手机", "mobile"]


def test_search_match_any_same_id_different_model_both_kept(cli, mock_api):
    _mock_search_by_query(
        mock_api,
        {
            "a": [_search_item(99, "card", "卡")],
            "b": [_search_item(99, "dashboard", "板")],
        },
        {"a": 1, "b": 1},
    )
    result = cli("--url", BASE, "--api-key", "k", "search", "a,b", "--match-any").assert_ok(0)
    assert [(i["model"], i["id"]) for i in result.json()] == [("card", 99), ("dashboard", 99)]


def test_search_match_any_with_limit_budgets_each_keyword(cli, mock_api):
    route = _mock_search_by_query(
        mock_api,
        {
            "手机": [_search_item(i, "card", f"手机{i}") for i in range(1, 8)],
            "mobile": [_search_item(20, "card", "mobile")],
        },
        {"手机": 7, "mobile": 1},
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "search", "手机,mobile", "--match-any", "--limit", "3"
    ).assert_ok(0)
    ids = [i["id"] for i in result.json()]
    assert ids == [1, 2, 3]  # merged then capped at the display limit
    # per-keyword request asks for exactly --limit rows
    for call in route.calls:
        assert call.request.url.params.get("limit") == "3"


def test_search_match_any_empty_keywords_is_invalid_args(cli, mock_api):
    result = cli("--url", BASE, "--api-key", "k", "search", " , ", "--match-any")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_search_match_any_markdown_format(cli, mock_api):
    _mock_search_by_query(
        mock_api,
        {"手机": [_search_item(1, "card", "手机卡")]},
        {"手机": 1},
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "search", "手机", "--match-any", "--format", "markdown"
    ).assert_ok(0)
    assert result.stdout.startswith("| id | model |")
    assert "手机卡" in result.stdout


# -- search: --limit 0 fetch-all --------------------------------------------------------


def test_search_limit_0_pages_until_total(cli, mock_api):
    pool = [_search_item(i, "card", f"数据{i}") for i in range(250)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 50))
        assert limit == 100  # fetch-all page size (§13.4)
        return httpx.Response(
            200, json=_search_page(pool[offset : offset + limit], 250, limit, offset)
        )

    route = mock_api.get(f"{BASE}/api/search").mock(side_effect=handler)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--limit", "0").assert_ok(0)
    parsed = result.json()
    assert isinstance(parsed, list) and len(parsed) == 250  # complete: no envelope
    offsets = [call.request.url.params.get("offset") for call in route.calls]
    assert offsets == ["0", "100", "200"]


def test_search_limit_0_hard_cap_annotates_json_envelope(cli, mock_api):
    pool = [_search_item(i, "card", f"数据{i}") for i in range(1500)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 50))
        return httpx.Response(
            200, json=_search_page(pool[offset : offset + limit], 1500, limit, offset)
        )

    mock_api.get(f"{BASE}/api/search").mock(side_effect=handler)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--limit", "0").assert_ok(0)
    payload = result.json()
    # §5.2-style annotation: envelope key form
    assert list(payload) == ["truncated", "rows_returned", "items"]
    assert payload["truncated"] is True
    assert payload["rows_returned"] == 1000
    assert len(payload["items"]) == 1000


def test_search_limit_0_ndjson_trailing_sentinel(cli, mock_api):
    pool = [_search_item(i, "card", f"数据{i}") for i in range(1500)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 50))
        return httpx.Response(
            200, json=_search_page(pool[offset : offset + limit], 1500, limit, offset)
        )

    mock_api.get(f"{BASE}/api/search").mock(side_effect=handler)
    result = cli(
        "--url", BASE, "--api-key", "k", "search", "数据", "--limit", "0", "--format", "ndjson"
    ).assert_ok(0)
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1001  # 1000 records + sentinel
    assert json.loads(lines[-1]) == {"truncated": True, "rows_returned": 1000}


def test_search_limit_0_exact_1000_still_annotates(cli, mock_api):
    """Reaching the hard cap ALWAYS annotates: the live instance reports
    total == 1000 for broad queries (a server-side total cap), so 'exactly
    1000 and complete' is indistinguishable from 'at least 1000'."""
    pool = [_search_item(i, "card", f"数据{i}") for i in range(1000)]

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 50))
        return httpx.Response(
            200, json=_search_page(pool[offset : offset + limit], 1000, limit, offset)
        )

    mock_api.get(f"{BASE}/api/search").mock(side_effect=handler)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--limit", "0").assert_ok(0)
    payload = result.json()
    assert payload["truncated"] is True
    assert payload["rows_returned"] == 1000
    assert len(payload["items"]) == 1000


def test_search_limit_positive_keeps_single_page_contract(cli, mock_api):
    route = _mock_search_by_query(
        mock_api, {"数据": [_search_item(i, "card", f"数据{i}") for i in range(20)]}, {"数据": 20}
    )
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--limit", "20").assert_ok(0)
    assert len(result.json()) == 20
    assert route.call_count == 1  # unchanged v0.1 contract: one request


def test_truncation_marker_all_formats(capsys):
    records = [{"id": i} for i in range(3)]
    for fmt, sniff in [
        (Format.csv, lambda t: t.endswith('# {"truncated": true, "rows_returned": 3}\n')),
        (Format.ndjson, lambda t: t.endswith('{"truncated": true, "rows_returned": 3}\n')),
        (Format.table, lambda t: '{"truncated": true, "rows_returned": 3}' in t),
        (Format.markdown, lambda t: t.endswith('{"truncated": true, "rows_returned": 3}\n')),
    ]:
        emit_records([dict(r) for r in records], fmt=fmt, truncated=True)
        text = capsys.readouterr().out
        assert sniff(text), f"{fmt}: {text!r}"


def test_truncation_feeds_out_summary_flag(capsys, tmp_path):
    target = tmp_path / "out.json"
    emit_records([{"id": 1}], fmt=Format.json, truncated=True, out=str(target))
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"out": str(target), "rows": 1, "truncated": True}


# -- fields --search ---------------------------------------------------------------------


def test_fields_search_golden_rows_and_reserved_flag(cli, mock_api):
    metadata_route = mock_get(mock_api, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    qm_route = mock_get(mock_api, f"{BASE}/api/table/3258/query_metadata", QUERY_METADATA_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--search", "year_month", "--db", "15"
    ).assert_ok(0)
    parsed = result.json()
    # hidden_stats is filtered by default: exactly one visible hit
    assert parsed == [
        {
            "table": "trade_events",
            "table_id": 3258,
            "column": "year_month",
            "base_type": "type/Text",
            "semantic_type": None,
            "is_pk": False,
            "fk_target": None,
            "reserved": True,  # MySQL 8.0 reserved word
        }
    ]
    assert list(parsed[0]) == [
        "table",
        "table_id",
        "column",
        "base_type",
        "semantic_type",
        "is_pk",
        "fk_target",
        "reserved",
    ]
    # ZERO extra requests: one metadata GET, never the per-table endpoint
    assert metadata_route.call_count == 1
    assert qm_route.call_count == 0


def test_fields_search_include_hidden(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "fields",
        "--search",
        "year_month",
        "--db",
        "15",
        "--include-hidden",
    ).assert_ok(0)
    assert [row["table"] for row in result.json()] == ["trade_events", "hidden_stats"]


def test_fields_search_case_insensitive_and_reserved_key_column(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "fields",
        "--search",
        "KEY",
        "--db",
        "15",
        "--include-hidden",
    ).assert_ok(0)
    rows = result.json()
    by_column = {row["column"]: row for row in rows}
    assert set(by_column) == {"event_key", "key"}
    assert by_column["event_key"]["reserved"] is False  # substring hit, not reserved itself
    assert by_column["key"]["reserved"] is True


def test_fields_search_fk_target_backresolved(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--search", "event_key", "--db", "15"
    ).assert_ok(0)
    assert result.json()[0]["fk_target"] == {"table": "dim_carrier", "field": "id"}


def test_fields_search_requires_db(cli, mock_api):
    result = cli("--url", BASE, "--api-key", "k", "fields", "--search", "year_month")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_fields_search_and_table_are_mutually_exclusive(cli, mock_api):
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--table", "3258", "--search", "x", "--db", "15"
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_fields_neither_table_nor_search_is_invalid_args(cli, mock_api):
    result = cli("--url", BASE, "--api-key", "k", "fields")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_fields_search_no_hits_is_empty_array(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/database/15/metadata", METADATA_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--search", "zzz", "--db", "15"
    ).assert_ok(0)
    assert result.stdout == "[]\n"


# -- reserved words (MySQL 8.0, pinned subset) ---------------------------------------------


@pytest.mark.parametrize(
    "word", ["year_month", "date", "order", "key", "select", "rank", "row_number", "groups"]
)
def test_reserved_pinned_subset(word):
    assert is_reserved(word)
    assert is_reserved(word.upper())
    assert is_reserved(f"`{word}`")


@pytest.mark.parametrize("word", ["phone", "mobile", "uid", "quant_data", "event_key", ""])
def test_non_reserved_words(word):
    assert not is_reserved(word)


def test_reserved_list_is_sane_size():
    # MySQL 8.0 has ~250 reserved words; guard against accidental wipe/shrink
    assert 230 <= len(MYSQL_RESERVED_WORDS) <= 280


def test_find_reserved_token_extractions():
    syntax = "You have an error in your SQL syntax; ... near 'year_month varchar(10)' at line 1"
    assert find_reserved_token(syntax) == "year_month"
    assert find_reserved_token("Unknown column 'order' in 'field list'") == "order"
    assert find_reserved_token("... near 'order int' at line 1") == "order"


def test_find_reserved_token_no_false_positives():
    assert find_reserved_token("Table 'quant_data.x' doesn't exist") is None
    assert find_reserved_token("Unknown column 'foo' in 'where clause'") is None
    assert find_reserved_token("simply no tokens of interest here") is None


# -- client: bad_query reserved-word hint ----------------------------------------------------


def test_bad_query_detail_appends_reserved_hint(mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "You have an error in your SQL syntax; check the manual"
                " ... near 'year_month varchar(10)' at line 1",
                "status": "failed",
                "error_type": "invalidQuery",
            },
        )
    )
    from mbcli.client import MetabaseClient
    from mbcli.config import Config

    client = MetabaseClient(
        Config(url=BASE, api_key="k", session_cache="/tmp/none.json"), retry_backoffs=()
    )
    with pytest.raises(Exception) as excinfo:
        client.post_json("/api/dataset", {})
    error = excinfo.value
    assert error.code == "bad_query"
    assert error.detail["reserved_word"] == "year_month"
    assert "反引号" in error.detail["hint"]
    assert "year_month" in error.detail["hint"]


def test_query_bad_query_reserved_hint_via_cli(cli, mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400,
            json={
                "error": "You have an error in your SQL syntax; ... near 'order int' at line 1",
                "status": "failed",
                "error_type": "invalidQuery",
            },
        )
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT order FROM t"
    )
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "bad_query"
    assert error["detail"]["reserved_word"] == "order"
    assert "反引号" in error["detail"]["hint"]


def test_bad_query_without_reserved_token_has_no_hint(cli, mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400, json={"error": "Table 'quant_data.x' doesn't exist", "status": "failed"}
        )
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT 1 FROM x"
    )
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "bad_query"
    assert "reserved_word" not in error["detail"]
    assert "hint" not in error["detail"]


# -- -v log destination: stdout stays pure data ------------------------------------------------


def test_verbose_logs_go_to_stderr_stdout_is_pure_json(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/database", DATABASES_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "-v", "databases", "list").assert_ok(0)
    assert result.stdout.count("\n") == 1  # exactly one JSON line
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, list)
    assert "request: GET /api/database" in result.stderr
    assert "response: 200 /api/database" in result.stderr


# -- v0.2 stubs: freshness / overview / batch ---------------------------------------------------


def _assert_not_implemented(result):
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "not_implemented"
    assert "not implemented" in error["message"]


def test_stub_freshness_not_implemented(cli):
    _assert_not_implemented(cli("freshness", "--db", "15"))


def test_stub_freshness_full_signature_parses(cli):
    _assert_not_implemented(
        cli(
            "freshness",
            "--db",
            "quant_data",
            "--table",
            "a",
            "--table",
            "b",
            "--max-tables",
            "5",
            "--sample",
            "--format",
            "csv",
        )
    )


def test_stub_freshness_max_tables_must_be_positive(cli):
    result = cli("freshness", "--db", "15", "--max-tables", "0")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_stub_overview_not_implemented(cli):
    _assert_not_implemented(cli("overview"))


def test_stub_overview_full_signature_parses(cli):
    _assert_not_implemented(cli("overview", "--top", "3", "--format", "markdown"))


def test_stub_batch_not_implemented(cli):
    _assert_not_implemented(cli("batch"))


def test_stubs_appear_in_help(cli):
    result = cli("--help").assert_ok(0)
    for name in ("freshness", "overview", "batch"):
        assert name in result.stdout


# -- collections show: tabular formats render the items array -----------------------------------


def test_collections_show_csv_renders_items_not_envelope(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "collections", "show", "root", "--format", "csv"
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert lines[0] == "id,model,name,description,collection,display,updated_at"
    # nested dict cells render as JSON (same rule as the query/csv contract)
    assert (
        lines[1]
        == '7876,card,数据日期,,"{""id"": 1039, ""name"": ""报表""}",scalar,2026-10-01T08:00:00.000Z'
    )
    assert "total" not in result.stdout  # envelope keys are not CSV columns


def test_collections_show_markdown_and_ndjson(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "collections", "show", "root", "--format", "markdown"
    ).assert_ok(0)
    assert result.stdout.startswith("| id | model | name |")
    assert "运营看板" in result.stdout
    result = cli(
        "--url", BASE, "--api-key", "k", "collections", "show", "root", "--format", "ndjson"
    ).assert_ok(0)
    records = [json.loads(line) for line in result.stdout.strip().splitlines()]
    assert [r["id"] for r in records] == [7876, 1607]


def test_collections_show_json_envelope_unchanged(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "show", "root").assert_ok(0)
    assert list(result.json()) == ["total", "limit", "offset", "items"]


def test_collections_show_fields_selects_csv_columns(cli, mock_api):
    mock_get(mock_api, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "collections",
        "show",
        "root",
        "--format",
        "csv",
        "--fields",
        "model,name",
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert lines[0] == "model,name"
    assert lines[1] == "card,数据日期"
