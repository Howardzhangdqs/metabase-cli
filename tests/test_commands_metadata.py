"""Metadata commands (DESIGN §4, §5.1): schemas / tables / fields — Phase B.

Golden tests pin the exact §5.1 JSON contracts against canned respx payloads
modeled on the real probe facts (DESIGN §0): quant_data is db id=15 with 328
tables, MySQL schema is "", table rows is null (only estimated_row_count),
fields carry base_type like "type/Text" / database_type like "VARCHAR", and
query_metadata hydrates FK `target` objects. All tests are offline; every
fixture/helper is local to this file (conftest stays frozen).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

BASE = "http://test.local"

DATABASES_PAYLOAD: dict[str, Any] = {
    "data": [
        {"id": 7, "name": "bi_data", "engine": "mysql"},
        {"id": 15, "name": "quant_data", "engine": "mysql", "description": "量化"},
    ],
    "total": 2,
}

DUP_DATABASES_PAYLOAD: dict[str, Any] = {
    "data": [
        {"id": 21, "name": "dup_db", "engine": "mysql"},
        {"id": 22, "name": "dup_db", "engine": "mysql"},
    ],
    "total": 2,
}


def _meta_field(fid: int, name: str, database_type: str = "VARCHAR") -> dict[str, Any]:
    """A metadata-table field entry (only the count matters for `tables`)."""
    return {
        "id": fid,
        "name": name,
        "display_name": name,
        "base_type": "type/Text",
        "semantic_type": None,
        "database_type": database_type,
        "database_is_pk": False,
    }


# quant_data (id=15) flavor: "" schema tables incl. hidden/cruft/inactive ones,
# plus one non-empty schema so filtering is observable.
QUANT_METADATA: dict[str, Any] = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "tables": [
        {
            "id": 3258,
            "schema": "",
            "name": "app_mau",
            "display_name": "app_mau",
            "description": None,
            "estimated_row_count": 1000000000,  # §0: severely distorted estimate
            "rows": None,  # §0: rows is already null on this instance
            "active": True,
            "visibility_type": None,
            "entity_type": "entity/GenericTable",  # unknown key -> dropped
            "fields": [_meta_field(37677, "date"), _meta_field(37678, "uid", "BIGINT")],
        },
        {
            "id": 3259,
            "schema": "",
            "name": "stock_daily",
            "display_name": "stock_daily",
            "description": "A股日线行情",
            "estimated_row_count": 65,
            "active": True,
            "visibility_type": None,
            "fields": [
                _meta_field(1, "code"),
                _meta_field(2, "dt", "DATE"),
                _meta_field(3, "close", "DECIMAL"),
            ],
        },
        {
            "id": 3260,
            "schema": "",
            "name": "hidden_agg",
            "display_name": "hidden_agg",
            "description": None,
            "estimated_row_count": 10,
            "active": True,
            "visibility_type": "hidden",
            "fields": [_meta_field(9, "x")],
        },
        {
            "id": 3261,
            "schema": "",
            "name": "tmp_cruft",
            "display_name": "tmp_cruft",
            "description": None,
            "estimated_row_count": 0,
            "active": True,
            "visibility_type": "cruft",
            "fields": [],
        },
        {
            "id": 3262,
            "schema": "",
            "name": "old_drop",
            "display_name": "old_drop",
            "description": None,
            "estimated_row_count": None,
            "active": False,
            "visibility_type": None,
            "fields": [_meta_field(8, "y")],
        },
        {
            "id": 4001,
            "schema": "public",
            "name": "users",
            "display_name": "users",
            "description": None,
            "estimated_row_count": 12,
            "active": True,
            "visibility_type": None,
            "fields": [_meta_field(11, "id", "BIGINT"), _meta_field(12, "email")],
        },
    ],
}


def _quant_like(n_tables: int) -> dict[str, Any]:
    """§0 flavor: quant_data really has 328 tables in a single '' schema."""
    return {
        "id": 15,
        "name": "quant_data",
        "engine": "mysql",
        "tables": [
            {
                "id": 3000 + i,
                "schema": "",
                "name": f"t_{i}",
                "display_name": f"t_{i}",
                "description": None,
                "estimated_row_count": i,
                "active": True,
                "visibility_type": None,
                "fields": [],
            }
            for i in range(n_tables)
        ],
    }


QUERY_METADATA_3258: dict[str, Any] = {
    "id": 3258,
    "name": "app_mau",
    "schema": "",
    "db_id": 15,
    "fields": [
        {
            "id": 37677,
            "name": "date",
            "display_name": "date",
            "base_type": "type/Text",
            "semantic_type": None,
            "database_type": "VARCHAR",
            "description": None,
            "database_is_pk": False,
            "database_is_nullable": True,
            "database_indexed": False,
            "database_position": 1,
            "position": 99,  # UI override — §5.1 `position` must come from database_position
            "target": None,
            "fk_target_field_id": None,
        },
        {
            "id": 37678,
            "name": "uid",
            "display_name": "uid",
            "base_type": "type/BigInteger",
            "semantic_type": "type/FK",
            "database_type": "BIGINT",
            "description": "用户外键",
            "database_is_pk": False,
            "database_is_nullable": False,
            "database_indexed": True,
            "database_position": 2,
            "position": 98,
            "target": {"id": 11, "name": "id", "table": {"id": 5, "name": "users", "schema": ""}},
        },
        {
            "id": 37679,
            "name": "id",
            "display_name": "id",
            "base_type": "type/BigInteger",
            "semantic_type": "type/PK",
            "database_type": "BIGINT",
            "description": None,
            "database_is_pk": True,
            "database_is_nullable": False,
            "database_indexed": True,
            "database_position": 3,
            "position": 97,
            "target": None,
        },
    ],
}

FIELDS_GOLDEN: list[dict[str, Any]] = [
    {
        "id": 37677,
        "name": "date",
        "display_name": "date",
        "base_type": "type/Text",
        "semantic_type": None,
        "database_type": "VARCHAR",
        "description": None,
        "is_pk": False,
        "is_nullable": True,
        "is_indexed": False,
        "fk_target": None,
        "position": 1,
    },
    {
        "id": 37678,
        "name": "uid",
        "display_name": "uid",
        "base_type": "type/BigInteger",
        "semantic_type": "type/FK",
        "database_type": "BIGINT",
        "description": "用户外键",
        "is_pk": False,
        "is_nullable": False,
        "is_indexed": True,
        "fk_target": {"table": "users", "field": "id"},
        "position": 2,
    },
    {
        "id": 37679,
        "name": "id",
        "display_name": "id",
        "base_type": "type/BigInteger",
        "semantic_type": "type/PK",
        "database_type": "BIGINT",
        "description": None,
        "is_pk": True,
        "is_nullable": False,
        "is_indexed": True,
        "fk_target": None,
        "position": 3,
    },
]

TABLES_GOLDEN: list[dict[str, Any]] = [
    {
        "id": 3258,
        "schema": "",
        "name": "app_mau",
        "display_name": "app_mau",
        "description": None,
        "estimated_row_count": 1000000000,
        "active": True,
        "field_count": 2,
    },
    {
        "id": 3259,
        "schema": "",
        "name": "stock_daily",
        "display_name": "stock_daily",
        "description": "A股日线行情",
        "estimated_row_count": 65,
        "active": True,
        "field_count": 3,
    },
    {
        "id": 4001,
        "schema": "public",
        "name": "users",
        "display_name": "users",
        "description": None,
        "estimated_row_count": 12,
        "active": True,
        "field_count": 2,
    },
]

DUP_TABLE_METADATA: dict[str, Any] = {
    "tables": [
        {"id": 500, "schema": "", "name": "orders", "active": True, "fields": []},
        {"id": 501, "schema": "", "name": "orders", "active": True, "fields": []},
    ]
}


# -- route helpers (local; conftest is frozen) ---------------------------------------


def mock_databases(mock, payload=None):
    return mock.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=payload if payload is not None else DATABASES_PAYLOAD)
    )


def mock_metadata(mock, payload=None, db_id: int = 15):
    return mock.get(f"{BASE}/api/database/{db_id}/metadata").mock(
        return_value=httpx.Response(200, json=payload if payload is not None else QUANT_METADATA)
    )


def mock_query_metadata(mock, payload=None, table_id: int = 3258):
    return mock.get(f"{BASE}/api/table/{table_id}/query_metadata").mock(
        return_value=httpx.Response(
            200, json=payload if payload is not None else QUERY_METADATA_3258
        )
    )


# -- schemas -------------------------------------------------------------------------


def test_schemas_golden_exact_json(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "15").assert_ok(0)
    assert result.json() == [{"name": "", "table_count": 5}, {"name": "public", "table_count": 1}]
    assert list(result.json()[0]) == ["name", "table_count"]
    assert (
        result.stdout
        == json.dumps(
            [{"name": "", "table_count": 5}, {"name": "public", "table_count": 1}],
            ensure_ascii=False,
        )
        + "\n"
    )


def test_schemas_mysql_single_empty_schema_328_tables(cli, mock_api):
    """§0 probe fact: quant_data has 328 tables, MySQL schema is ''."""
    mock_databases(mock_api)
    mock_metadata(mock_api, payload=_quant_like(328))
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "quant_data").assert_ok(0)
    assert result.stdout == '[{"name": "", "table_count": 328}]\n'


def test_schemas_by_name_resolves_via_database_list(cli, mock_api):
    route = mock_databases(mock_api)
    mock_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "schemas", "--db", "quant_data").assert_ok(0)
    assert route.call_count == 1


def test_schemas_by_id_never_lists_databases(cli, mock_api):
    route = mock_databases(mock_api)
    mock_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "schemas", "--db", "15").assert_ok(0)
    assert route.call_count == 0


def test_schemas_empty_database_yields_empty_array(cli, mock_api):
    mock_metadata(mock_api, payload={"tables": []})
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "15").assert_ok(0)
    assert result.stdout == "[]\n"


def test_schemas_fields_prune(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "schemas", "--db", "15", "--fields", "name"
    ).assert_ok(0)
    assert result.json() == [{"name": ""}, {"name": "public"}]


def test_schemas_unknown_db_name_not_found_exit_1(cli, mock_api):
    mock_databases(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "nope")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_schemas_ambiguous_db_name_exit_1(cli, mock_api):
    mock_databases(mock_api, payload=DUP_DATABASES_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "dup_db")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "ambiguous_ref"
    assert sorted(c["id"] for c in error["detail"]["candidates"]) == [21, 22]


def test_schemas_metadata_500_is_server_error(cli, mock_api):
    mock_api.get(f"{BASE}/api/database/15/metadata").mock(
        return_value=httpx.Response(500, text="down")
    )
    result = cli("--url", BASE, "--api-key", "k", "schemas", "--db", "15")
    assert result.exit_code == 1
    assert result.error()["code"] == "server_error"


# -- tables --------------------------------------------------------------------------


def test_tables_golden_exact_json(cli, mock_api):
    """Default view: hidden/cruft/inactive tables dropped; §5.1 key order exact;
    unknown upstream keys (rows, entity_type) never leak."""
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15").assert_ok(0)
    parsed = result.json()
    assert parsed == TABLES_GOLDEN
    assert list(parsed[0]) == [
        "id",
        "schema",
        "name",
        "display_name",
        "description",
        "estimated_row_count",
        "active",
        "field_count",
    ]
    assert result.stdout == json.dumps(TABLES_GOLDEN, ensure_ascii=False) + "\n"


def test_tables_estimated_row_count_passthrough_and_null(cli, mock_api):
    payload = {
        "tables": [
            {
                "id": 1,
                "schema": "",
                "name": "a",
                "estimated_row_count": 1234,
                "active": True,
                "visibility_type": None,
                "fields": [],
            },
            {
                "id": 2,
                "schema": "",
                "name": "b",
                "estimated_row_count": None,
                "active": True,
                "visibility_type": None,
                "fields": [],
            },
        ]
    }
    mock_metadata(mock_api, payload=payload)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15").assert_ok(0)
    assert [t["estimated_row_count"] for t in result.json()] == [1234, None]


def test_tables_schema_filter(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--schema", "public")
    assert_ok = result.assert_ok(0)
    assert [t["name"] for t in assert_ok.json()] == ["users"]


def test_tables_schema_empty_string_matches_mysql_tables(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--schema", "")
    names = [t["name"] for t in result.assert_ok(0).json()]
    assert names == ["app_mau", "stock_daily"]  # hidden/cruft/inactive already filtered


def test_tables_match_case_insensitive(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--match", "STOCK")
    assert [t["name"] for t in result.assert_ok(0).json()] == ["stock_daily"]


def test_tables_match_substring_anywhere(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--match", "mau")
    assert [t["name"] for t in result.assert_ok(0).json()] == ["app_mau"]


def test_tables_match_no_hits_is_empty_array(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--match", "zzz")
    assert result.assert_ok(0).stdout == "[]\n"


def test_tables_match_and_schema_combine(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "tables",
        "--db",
        "15",
        "--schema",
        "public",
        "--match",
        "USE",
    )
    assert [t["name"] for t in result.assert_ok(0).json()] == ["users"]
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "tables",
        "--db",
        "15",
        "--schema",
        "",
        "--match",
        "USE",
    )
    assert result.assert_ok(0).stdout == "[]\n"


def test_tables_include_hidden_param_passthrough_and_unfiltered(cli, mock_api):
    route = mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "tables", "--db", "15", "--include-hidden"
    ).assert_ok(0)
    request = route.calls.last.request
    assert request.url.params.get("include_hidden") == "true"
    names = [t["name"] for t in result.json()]
    assert names == ["app_mau", "stock_daily", "hidden_agg", "tmp_cruft", "old_drop", "users"]


def test_tables_default_request_has_no_include_hidden(cli, mock_api):
    route = mock_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "tables", "--db", "15").assert_ok(0)
    assert "include_hidden" not in route.calls.last.request.url.params
    assert "?" not in str(route.calls.last.request.url)


def test_tables_include_hidden_inactive_flag_still_reported(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "tables", "--db", "15", "--include-hidden"
    ).assert_ok(0)
    old_drop = next(t for t in result.json() if t["name"] == "old_drop")
    assert old_drop["active"] is False  # passthrough, no client-side filtering


def test_tables_by_id_never_lists_databases(cli, mock_api):
    db_list = mock_databases(mock_api)
    mock_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "tables", "--db", "15").assert_ok(0)
    assert db_list.call_count == 0


def test_tables_by_name_lists_databases_once(cli, mock_api):
    db_list = mock_databases(mock_api)
    metadata = mock_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "tables", "--db", "quant_data").assert_ok(0)
    assert db_list.call_count == 1
    assert metadata.call_count == 1


def test_tables_fields_prune(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "tables", "--db", "15", "--fields", "id,name"
    ).assert_ok(0)
    assert result.json() == [
        {"id": 3258, "name": "app_mau"},
        {"id": 3259, "name": "stock_daily"},
        {"id": 4001, "name": "users"},
    ]


def test_tables_csv_format(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "tables",
        "--db",
        "15",
        "--format",
        "csv",
        "--fields",
        "id,name",
    ).assert_ok(0)
    assert result.stdout == "id,name\r\n3258,app_mau\r\n3259,stock_daily\r\n4001,users\r\n"


def test_tables_ndjson_format_one_record_per_line(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "tables",
        "--db",
        "15",
        "--format",
        "ndjson",
        "--fields",
        "name",
    ).assert_ok(0)
    assert result.stdout == '{"name": "app_mau"}\n{"name": "stock_daily"}\n{"name": "users"}\n'


def test_tables_bad_fields_exit_2(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--fields", "id,nope")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_tables_include_hidden_non_object_response_invalid_response(cli, mock_api):
    mock_api.get(f"{BASE}/api/database/15/metadata").mock(
        return_value=httpx.Response(200, json=[1, 2, 3])
    )
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15", "--include-hidden")
    assert result.exit_code == 1
    assert result.error()["code"] == "invalid_response"


def test_tables_metadata_404_not_found(cli, mock_api):
    mock_api.get(f"{BASE}/api/database/15/metadata").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    result = cli("--url", BASE, "--api-key", "k", "tables", "--db", "15")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


# -- fields --------------------------------------------------------------------------


def test_fields_golden_exact_json(cli, mock_api):
    mock_query_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    parsed = result.json()
    assert parsed == FIELDS_GOLDEN
    assert list(parsed[0]) == [
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
    ]
    assert result.stdout == json.dumps(FIELDS_GOLDEN, ensure_ascii=False) + "\n"


def test_fields_fk_target_hydrated_from_target(cli, mock_api):
    mock_query_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    uid = next(f for f in result.json() if f["name"] == "uid")
    assert uid["fk_target"] == {"table": "users", "field": "id"}


def test_fields_fk_target_null_when_target_absent(cli, mock_api):
    payload = {
        "id": 1,
        "fields": [
            {
                "id": 10,
                "name": "plain",
                "base_type": "type/Text",
                "database_type": "VARCHAR",
                "database_position": 1,
            }
        ],
    }
    mock_query_metadata(mock_api, payload=payload, table_id=1)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "1").assert_ok(0)
    assert result.json()[0]["fk_target"] is None


def test_fields_fk_target_null_when_target_has_no_table(cli, mock_api):
    payload = {
        "id": 1,
        "fields": [
            {
                "id": 10,
                "name": "dangling",
                "database_position": 1,
                "target": {"id": 11, "name": "id"},  # no table object -> cannot hydrate
            }
        ],
    }
    mock_query_metadata(mock_api, payload=payload, table_id=1)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "1").assert_ok(0)
    assert result.json()[0]["fk_target"] is None


def test_fields_position_comes_from_database_position(cli, mock_api):
    mock_query_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    # payload deliberately sets position=99/98/97 vs database_position=1/2/3
    assert [f["position"] for f in result.json()] == [1, 2, 3]


def test_fields_is_pk_is_nullable_is_indexed_from_database_flags(cli, mock_api):
    mock_query_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    by_name = {f["name"]: f for f in result.json()}
    assert by_name["id"]["is_pk"] is True
    assert by_name["date"]["is_pk"] is False
    assert by_name["date"]["is_nullable"] is True
    assert by_name["uid"]["is_nullable"] is False
    assert by_name["uid"]["is_indexed"] is True
    assert by_name["date"]["is_indexed"] is False


def test_fields_table_id_without_db_single_request(cli, mock_api):
    db_list = mock_databases(mock_api)
    metadata = mock_metadata(mock_api)
    qm = mock_query_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    assert db_list.call_count == 0
    assert metadata.call_count == 0
    assert qm.call_count == 1


def test_fields_table_name_with_db_resolves_via_metadata_cache(cli, mock_api):
    db_list = mock_databases(mock_api)
    metadata = mock_metadata(mock_api)
    qm = mock_query_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--table", "app_mau", "--db", "quant_data"
    ).assert_ok(0)
    assert len(result.json()) == 3
    assert db_list.call_count == 1
    assert metadata.call_count == 1  # resolution reuses the cached metadata
    assert qm.call_count == 1


def test_fields_table_name_without_db_is_invalid_args_exit_2(cli, mock_api):
    mock_databases(mock_api)
    metadata = mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "app_mau")
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert "--db" in error["message"]
    assert metadata.call_count == 0  # fails before any metadata fetch


def test_fields_ambiguous_table_name_exit_1(cli, mock_api):
    mock_metadata(mock_api, payload=DUP_TABLE_METADATA)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "orders", "--db", "15")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "ambiguous_ref"
    assert sorted(c["id"] for c in error["detail"]["candidates"]) == [500, 501]


def test_fields_unknown_table_name_not_found_exit_1(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "nope", "--db", "15")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_fields_include_hidden_fields_param_passthrough(cli, mock_api):
    qm = mock_query_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258", "--include-hidden").assert_ok(
        0
    )
    assert qm.calls.last.request.url.params.get("include_hidden_fields") == "true"


def test_fields_default_request_has_no_params(cli, mock_api):
    qm = mock_query_metadata(mock_api)
    cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258").assert_ok(0)
    assert str(qm.calls.last.request.url).endswith("/api/table/3258/query_metadata")
    assert "?" not in str(qm.calls.last.request.url)


def test_fields_empty_fields_array(cli, mock_api):
    mock_query_metadata(mock_api, payload={"id": 1, "fields": []}, table_id=1)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "1").assert_ok(0)
    assert result.stdout == "[]\n"


def test_fields_prune(cli, mock_api):
    mock_query_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "fields", "--table", "3258", "--fields", "name,is_pk"
    ).assert_ok(0)
    assert result.json() == [
        {"name": "date", "is_pk": False},
        {"name": "uid", "is_pk": False},
        {"name": "id", "is_pk": True},
    ]


def test_fields_query_metadata_404_not_found_exit_1(cli, mock_api):
    mock_api.get(f"{BASE}/api/table/99999/query_metadata").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "99999")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_fields_non_object_response_invalid_response(cli, mock_api):
    mock_api.get(f"{BASE}/api/table/3258/query_metadata").mock(
        return_value=httpx.Response(200, json="nope")
    )
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "3258")
    assert result.exit_code == 1
    assert result.error()["code"] == "invalid_response"


def test_fields_position_null_when_database_position_missing(cli, mock_api):
    payload = {
        "id": 1,
        "fields": [{"id": 10, "name": "x", "database_type": "INT"}],  # no database_position
    }
    mock_query_metadata(mock_api, payload=payload, table_id=1)
    result = cli("--url", BASE, "--api-key", "k", "fields", "--table", "1").assert_ok(0)
    assert result.json()[0]["position"] is None


# -- cross-command contract -----------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ("schemas", "--db", "15"),
        ("tables", "--db", "15"),
        ("fields", "--table", "3258"),
    ],
)
def test_metadata_commands_single_line_json_only_on_stdout(cli, mock_api, argv):
    mock_databases(mock_api)
    mock_metadata(mock_api)
    mock_query_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", *argv).assert_ok(0)
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1  # exactly one JSON line
    json.loads(result.stdout)
