"""Golden/behavior tests for `mbcli context` (DESIGN §6 — the differentiator).

The markdown golden pins the EXACT output layout: header line, per-table
heading with estimated_row_count ¹ marker, `> description` line when present,
the 6-column field table (FK resolved via fk_target_field_id back-reference
within the same response), and the trailing footnote. Offline only (respx).
"""

from __future__ import annotations

import httpx
import pytest

BASE = "http://test.local"

FOOTNOTE = "¹ estimated_row_count 为 Metabase 同步估值，仅作参考"

DATABASES_FOR_NAME = {
    "data": [
        {"id": 13, "name": "bi_data"},
        {"id": 15, "name": "quant_data"},
    ],
    "total": 2,
}


def field(
    fid: int,
    name: str,
    base: str,
    db_type: str,
    *,
    semantic: str | None = None,
    description: str | None = None,
    is_pk: bool = False,
    fk: int | None = None,
    nullable: bool = True,
    indexed: bool = False,
    position: int = 1,
) -> dict:
    return {
        "id": fid,
        "name": name,
        "display_name": name,
        "base_type": base,
        "semantic_type": semantic,
        "database_type": db_type,
        "description": description,
        "database_is_pk": is_pk,
        "database_is_nullable": nullable,
        "database_indexed": indexed,
        "fk_target_field_id": fk,
        "position": position,
    }


METADATA = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "description": "量化",
    "is_sample": False,
    "initial_sync_status": "complete",
    "timezone": "UTC",
    "dbms_version": {"flavor": "MySQL", "version": "8.0.33"},
    "tables": [
        {
            "id": 3250,
            "schema": "",
            "name": "aaa_first",
            "display_name": "aaa_first",
            "description": None,
            "estimated_row_count": 0,
            "active": True,
            "fields": [],
        },
        {
            "id": 3258,
            "schema": "",
            "name": "app_mau",
            "display_name": "app_mau",
            "description": "日活表",
            "estimated_row_count": 65,
            "active": True,
            "fields": [
                field(37677, "date", "type/Text", "VARCHAR"),
                field(
                    37678,
                    "uid",
                    "type/BigInteger",
                    "BIGINT",
                    semantic="type/FK",
                    description="用户外键",
                    fk=38001,
                    nullable=False,
                    indexed=True,
                    position=2,
                ),
            ],
        },
        {
            "id": 3260,
            "schema": "",
            "name": "users",
            "display_name": "users",
            "description": None,
            "estimated_row_count": 1200,
            "active": True,
            "fields": [
                field(
                    38001,
                    "id",
                    "type/BigInteger",
                    "BIGINT",
                    semantic="type/PK",
                    description="主键",
                    is_pk=True,
                    nullable=False,
                    indexed=True,
                ),
                field(38002, "email", "type/Text", "VARCHAR", position=2),
            ],
        },
    ],
}

MARKDOWN_GOLDEN = (
    "# Database: quant_data (mysql, id=15) — 3 tables shown / 3 total\n"
    "\n"
    "## Table `aaa_first` (id=3250, ~0 rows¹)\n"
    "\n"
    "| column | type | semantic | pk | fk → | description |\n"
    "|---|---|---|---|---|---|\n"
    "\n"
    "## Table `app_mau` (id=3258, ~65 rows¹)\n"
    "> 日活表\n"
    "\n"
    "| column | type | semantic | pk | fk → | description |\n"
    "|---|---|---|---|---|---|\n"
    "| date | VARCHAR | type/Text | | | |\n"
    "| uid | BIGINT | type/FK | | users.id | 用户外键 |\n"
    "\n"
    "## Table `users` (id=3260, ~1200 rows¹)\n"
    "\n"
    "| column | type | semantic | pk | fk → | description |\n"
    "|---|---|---|---|---|---|\n"
    "| id | BIGINT | type/PK | PK | | 主键 |\n"
    "| email | VARCHAR | type/Text | | | |\n"
    "\n"
    f"{FOOTNOTE}\n"
)

JSON_GOLDEN = {
    "database": {
        "id": 15,
        "name": "quant_data",
        "engine": "mysql",
        "description": "量化",
        "is_sample": False,
        "initial_sync_status": "complete",
        "timezone": "UTC",
        "dbms_version": {"flavor": "MySQL", "version": "8.0.33"},
    },
    "tables": [
        {
            "id": 3250,
            "schema": "",
            "name": "aaa_first",
            "display_name": "aaa_first",
            "description": None,
            "estimated_row_count": 0,
            "active": True,
            "field_count": 0,
            "fields": [],
        },
        {
            "id": 3258,
            "schema": "",
            "name": "app_mau",
            "display_name": "app_mau",
            "description": "日活表",
            "estimated_row_count": 65,
            "active": True,
            "field_count": 2,
            "fields": [
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
            ],
        },
        {
            "id": 3260,
            "schema": "",
            "name": "users",
            "display_name": "users",
            "description": None,
            "estimated_row_count": 1200,
            "active": True,
            "field_count": 2,
            "fields": [
                {
                    "id": 38001,
                    "name": "id",
                    "display_name": "id",
                    "base_type": "type/BigInteger",
                    "semantic_type": "type/PK",
                    "database_type": "BIGINT",
                    "description": "主键",
                    "is_pk": True,
                    "is_nullable": False,
                    "is_indexed": True,
                    "fk_target": None,
                    "position": 1,
                },
                {
                    "id": 38002,
                    "name": "email",
                    "display_name": "email",
                    "base_type": "type/Text",
                    "semantic_type": None,
                    "database_type": "VARCHAR",
                    "description": None,
                    "is_pk": False,
                    "is_nullable": True,
                    "is_indexed": False,
                    "fk_target": None,
                    "position": 2,
                },
            ],
        },
    ],
}


def mock_metadata(mock_api, payload=None):
    route = mock_api.get(f"{BASE}/api/database/15/metadata")
    route.mock(return_value=httpx.Response(200, json=payload or METADATA))
    return route


# -- markdown golden -------------------------------------------------------------------


def test_context_markdown_golden(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    assert result.stdout == MARKDOWN_GOLDEN


def test_context_explicit_markdown_flag_matches_default(cli, mock_api):
    mock_metadata(mock_api)
    default = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    explicit = cli(
        "--url", BASE, "--api-key", "k", "context", "--db", "15", "--markdown"
    ).assert_ok(0)
    assert explicit.stdout == default.stdout == MARKDOWN_GOLDEN


def test_context_fk_resolved_in_response_no_extra_requests(cli, mock_api):
    route = mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    assert "| users.id |" in result.stdout
    assert route.call_count == 1  # FK back-reference costs zero extra requests


# -- db / table resolution ----------------------------------------------------------------


def test_context_db_by_name(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_FOR_NAME)
    )
    route = mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "quant_data").assert_ok(0)
    assert result.stdout.startswith("# Database: quant_data (mysql, id=15) — 3 tables shown")
    assert route.call_count == 1


def test_context_db_unknown_name_not_found(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_FOR_NAME)
    )
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "nope")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_context_table_selection_preserves_given_order(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "context",
        "--db",
        "15",
        "--table",
        "users",
        "--table",
        "app_mau",
    ).assert_ok(0)
    assert result.stdout.startswith(
        "# Database: quant_data (mysql, id=15) — 2 tables shown / 3 total"
    )
    assert result.stdout.index("## Table `users`") < result.stdout.index("## Table `app_mau`")
    assert "aaa_first" not in result.stdout


def test_context_table_by_numeric_id(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15", "--table", "3258")
    result.assert_ok(0)
    assert result.stdout.startswith(
        "# Database: quant_data (mysql, id=15) — 1 tables shown / 3 total"
    )
    assert "## Table `app_mau`" in result.stdout


def test_context_missing_table_not_found(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15", "--table", "nope")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "not_found"
    assert error["detail"]["kind"] == "table"


def test_context_ambiguous_table_name(cli, mock_api):
    payload = {
        **METADATA,
        "tables": [
            {"id": 1, "name": "dup", "estimated_row_count": 1, "fields": []},
            {"id": 2, "name": "dup", "estimated_row_count": 2, "fields": []},
        ],
    }
    mock_metadata(mock_api, payload)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15", "--table", "dup")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "ambiguous_ref"
    assert sorted(c["id"] for c in error["detail"]["candidates"]) == [1, 2]


# -- max-tables / field pattern -----------------------------------------------------------


def test_context_max_tables_alphabetical_cutoff(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "context", "--db", "15", "--max-tables", "2"
    ).assert_ok(0)
    assert result.stdout.startswith(
        "# Database: quant_data (mysql, id=15) — 2 tables shown / 3 total"
    )
    assert "aaa_first" in result.stdout and "app_mau" in result.stdout
    assert "## Table `users`" not in result.stdout  # alphabetical cutoff is deterministic


def test_context_max_tables_default_50_shows_all(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    assert "— 3 tables shown / 3 total" in result.stdout


def test_context_fields_pattern(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "context",
        "--db",
        "15",
        "--include-fields-pattern",
        "^(uid|id)$",
    ).assert_ok(0)
    assert "| uid | BIGINT | type/FK | | users.id | 用户外键 |" in result.stdout
    assert "| id | BIGINT | type/PK | PK | | 主键 |" in result.stdout
    assert "| date |" not in result.stdout
    assert "| email |" not in result.stdout


def test_context_fields_pattern_no_match_keeps_structure(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "context",
        "--db",
        "15",
        "--include-fields-pattern",
        "^zzz$",
    ).assert_ok(0)
    assert result.stdout.count("| column | type | semantic | pk | fk → | description |") == 3
    assert "| date |" not in result.stdout
    assert result.stdout.endswith(FOOTNOTE + "\n")


def test_context_invalid_pattern_exit_2_before_http(cli, mock_api):
    route = mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "context",
        "--db",
        "15",
        "--include-fields-pattern",
        "[",
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"
    assert route.call_count == 0  # fail fast, no request with a broken regex


# -- json output ---------------------------------------------------------------------------


def test_context_json_golden(cli, mock_api):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15", "--json").assert_ok(0)
    assert result.json() == JSON_GOLDEN
    assert list(result.json()) == ["database", "tables"]
    assert list(result.json()["tables"][1]) == [
        "id",
        "schema",
        "name",
        "display_name",
        "description",
        "estimated_row_count",
        "active",
        "field_count",
        "fields",
    ]


def test_context_json_field_count_follows_filter(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "context",
        "--db",
        "15",
        "--json",
        "--include-fields-pattern",
        "^uid$",
    ).assert_ok(0)
    parsed = result.json()
    counts = {t["name"]: t["field_count"] for t in parsed["tables"]}
    assert counts == {"aaa_first": 0, "app_mau": 1, "users": 0}
    app_mau = parsed["tables"][1]
    assert [f["name"] for f in app_mau["fields"]] == ["uid"]
    assert app_mau["fields"][0]["fk_target"] == {"table": "users", "field": "id"}


def test_context_json_with_table_selection(cli, mock_api):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "context", "--db", "15", "--table", "users", "--json"
    ).assert_ok(0)
    parsed = result.json()
    assert list(parsed) == ["database", "tables"]
    assert [t["name"] for t in parsed["tables"]] == ["users"]
    assert [f["name"] for f in parsed["tables"][0]["fields"]] == ["id", "email"]


# -- edge cases ------------------------------------------------------------------------------


def test_context_estimated_row_count_none_placeholder(cli, mock_api):
    payload = {
        **METADATA,
        "tables": [
            {"id": 9, "name": "mystery", "estimated_row_count": None, "fields": []},
        ],
    }
    mock_metadata(mock_api, payload)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    assert "## Table `mystery` (id=9, ~? rows¹)" in result.stdout


def test_context_no_tables_at_all(cli, mock_api):
    mock_metadata(mock_api, {**METADATA, "tables": []})
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", "15").assert_ok(0)
    assert result.stdout == (
        "# Database: quant_data (mysql, id=15) — 0 tables shown / 0 total\n\n" + FOOTNOTE + "\n"
    )


@pytest.mark.parametrize("ref", ["15", "quant_data"])
def test_context_header_exact_string(cli, mock_api, ref):
    if ref == "quant_data":
        mock_api.get(f"{BASE}/api/database").mock(
            return_value=httpx.Response(200, json=DATABASES_FOR_NAME)
        )
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "context", "--db", ref).assert_ok(0)
    assert result.stdout.splitlines()[0] == (
        "# Database: quant_data (mysql, id=15) — 3 tables shown / 3 total"
    )
