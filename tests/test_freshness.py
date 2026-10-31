"""Behavior/golden tests for `mbcli freshness` (DESIGN §13.1; v0.2 wave 2).

Canned metadata is modeled on the LIVE probe of db 15 (v0.66): temporal
fingerprints nest bounds at fingerprint.type["type/DateTime"].{earliest,latest},
plain null and {"global":…}-only fingerprints exist too, and a date-NAMED
varchar column carries a type/Text fingerprint without any temporal bounds.
The flat {"min","max"} variant from the design doc is covered as a tolerated
shape. Offline only (respx); zero real network.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from mbcli.commands.freshness import (
    _fingerprint_bounds,
    _pick_time_column,
    _quote_ident,
    _sample_sql,
)

BASE = "http://test.local"

DATABASES_FOR_NAME = {
    "data": [
        {"id": 13, "name": "bi_data"},
        {"id": 15, "name": "quant_data"},
    ],
    "total": 2,
}


# -- fixture builders -------------------------------------------------------------------


def fp_datetime(low: str, high: str) -> dict:
    """Live v0.66 shape: temporal bounds nested in fingerprint.type."""
    return {
        "global": {"distinct-count": 10, "nil%": 0.0},
        "type": {
            "type/DateTime": {
                "earliest": low,
                "latest": high,
                "mode-fraction": 0.5,
                "top-3-fraction": 0.6,
            }
        },
    }


def fcol(
    fid: int,
    name: str,
    base: str = "type/Text",
    *,
    semantic: str | None = None,
    db_position: int | None = None,
    position: int | None = None,
    fingerprint: Any = None,
    has_fingerprint: bool = True,
) -> dict:
    field: dict[str, Any] = {
        "id": fid,
        "name": name,
        "display_name": name,
        "base_type": base,
        "semantic_type": semantic,
        "database_type": "VARCHAR",
        "database_position": db_position,
        "position": position,
    }
    if has_fingerprint:
        field["fingerprint"] = fingerprint
    return field


def tentry(tid: int, name: str, fields: list[dict], schema: str = "") -> dict:
    return {
        "id": tid,
        "schema": schema,
        "name": name,
        "display_name": name,
        "description": None,
        "estimated_row_count": 10,
        "active": True,
        "fields": fields,
    }


METADATA: dict[str, Any] = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "tables": [
        # semantic tier: two candidates -> lowest database_position wins
        tentry(
            101,
            "aaa_semantic",
            [
                fcol(1, "id", "type/BigInteger", db_position=1),
                fcol(
                    2,
                    "updated_at",
                    "type/DateTime",
                    semantic="type/UpdatedAt",
                    db_position=2,
                    fingerprint=fp_datetime("2024-01-01T00:00:00Z", "2024-06-30T00:00:00Z"),
                ),
                fcol(
                    3,
                    "created_at",
                    "type/DateTime",
                    semantic="type/CreationTimestamp",
                    db_position=3,
                    fingerprint=fp_datetime("2024-01-02T00:00:00Z", "2024-07-01T00:00:00Z"),
                ),
            ],
        ),
        # base-type tier beats the name-regex candidate regardless of position
        tentry(
            102,
            "bbb_base",
            [
                fcol(4, "id", "type/BigInteger", db_position=1),
                fcol(5, "snap_date", db_position=2, fingerprint={"type": {"type/Text": {}}}),
                fcol(
                    6,
                    "trade_time",
                    "type/DateTime",
                    db_position=5,
                    fingerprint=fp_datetime("2023-01-01T00:00:00Z", "2026-08-01T00:00:00Z"),
                ),
            ],
        ),
        # name-regex pick whose fingerprint exists but has no temporal bounds
        tentry(
            103,
            "ccc_nameregex",
            [
                fcol(7, "id", "type/BigInteger", db_position=1),
                fcol(8, "report_date", db_position=2, fingerprint={"type": {"type/Text": {}}}),
            ],
        ),
        # no time column at all
        tentry(
            104,
            "ddd_notime",
            [
                fcol(9, "id", "type/BigInteger", db_position=1),
                fcol(10, "value", "type/Float", db_position=2),
            ],
        ),
        # flat fingerprint variant (design-doc shape, tolerated)
        tentry(
            105,
            "eee_flat",
            [
                fcol(
                    11,
                    "dt",
                    "type/DateTime",
                    db_position=1,
                    fingerprint={"min": "2019-01-01", "max": "2026-08-01"},
                )
            ],
        ),
        # fingerprint explicitly null
        tentry(
            106,
            "fff_nullfp",
            [fcol(12, "created", "type/DateTime", db_position=1, fingerprint=None)],
        ),
        # fingerprint key absent from the payload entirely
        tentry(
            107, "ggg_missingfp", [fcol(13, "login_time", db_position=1, has_fingerprint=False)]
        ),
        # reserved-word column picked by name; global-only fingerprint (no bounds)
        tentry(
            108,
            "hhh_reserved",
            [
                fcol(14, "id", "type/BigInteger", db_position=1),
                fcol(
                    15, "year_month", db_position=2, fingerprint={"global": {"distinct-count": 5}}
                ),
            ],
        ),
        # type/Date sub-fingerprint
        tentry(
            109,
            "iii_datesub",
            [
                fcol(
                    16,
                    "d_day",
                    "type/Date",
                    db_position=1,
                    fingerprint={
                        "type": {"type/Date": {"earliest": "2026-01-01", "latest": "2026-03-01"}}
                    },
                )
            ],
        ),
        # name-regex pick over an INT column; Number fingerprint carries min/max
        tentry(
            110,
            "jjj_number",
            [
                fcol(
                    17,
                    "fiscal_year",
                    "type/BigInteger",
                    db_position=1,
                    fingerprint={"type": {"type/Number": {"min": 2019, "max": 2026}}},
                )
            ],
        ),
        # semantic candidate at a WORSE position still beats the base-type one
        tentry(
            111,
            "kkk_priority",
            [
                fcol(
                    18,
                    "event_date",
                    "type/DateTime",
                    db_position=1,
                    fingerprint=fp_datetime("2020-01-01T00:00:00Z", "2020-12-31T00:00:00Z"),
                ),
                fcol(
                    19,
                    "created_ts",
                    "type/DateTime",
                    semantic="type/CreationTimestamp",
                    db_position=9,
                    fingerprint=fp_datetime("2020-05-05T00:00:00Z", "2026-02-02T00:00:00Z"),
                ),
            ],
        ),
        # temporal fingerprint present but both bounds null -> unusable
        tentry(
            112,
            "lll_nullbounds",
            [
                fcol(
                    20,
                    "created",
                    "type/DateTime",
                    db_position=1,
                    fingerprint={"type": {"type/DateTime": {"earliest": None, "latest": None}}},
                )
            ],
        ),
    ],
}


def mock_metadata(mock_api: Any, payload: dict | None = None) -> Any:
    route = mock_api.get(f"{BASE}/api/database/15/metadata")
    route.mock(return_value=httpx.Response(200, json=payload or METADATA))
    return route


def mock_dataset(mock_api: Any, rows_by_sql: dict[str, list] | None = None) -> Any:
    """POST /api/dataset returning canned rows, keyed by the exact SQL."""

    def handler(request: httpx.Request) -> httpx.Response:
        sql = json.loads(request.content)["native"]["query"]
        rows = (rows_by_sql or {}).get(sql, [["2019-01-01", "2026-08-01"]])
        return httpx.Response(
            200, json={"status": "completed", "data": {"rows": rows, "row_count": len(rows)}}
        )

    return mock_api.post(f"{BASE}/api/dataset").mock(side_effect=handler)


def request_sql(call: Any) -> str:
    return json.loads(call.request.content)["native"]["query"]


# -- full-run golden ---------------------------------------------------------------------


def test_default_run_rows_golden(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    assert result.json() == [
        {
            "table": "aaa_semantic",
            "table_id": 101,
            "column": "updated_at",
            "min": "2024-01-01T00:00:00Z",
            "max": "2024-06-30T00:00:00Z",
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "bbb_base",
            "table_id": 102,
            "column": "trade_time",
            "min": "2023-01-01T00:00:00Z",
            "max": "2026-08-01T00:00:00Z",
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "ccc_nameregex",
            "table_id": 103,
            "column": "report_date",
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no fingerprint (use --sample)",
        },
        {
            "table": "ddd_notime",
            "table_id": 104,
            "column": None,
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no time column",
        },
        {
            "table": "eee_flat",
            "table_id": 105,
            "column": "dt",
            "min": "2019-01-01",
            "max": "2026-08-01",
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "fff_nullfp",
            "table_id": 106,
            "column": "created",
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no fingerprint (use --sample)",
        },
        {
            "table": "ggg_missingfp",
            "table_id": 107,
            "column": "login_time",
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no fingerprint (use --sample)",
        },
        {
            "table": "hhh_reserved",
            "table_id": 108,
            "column": "year_month",
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no fingerprint (use --sample)",
        },
        {
            "table": "iii_datesub",
            "table_id": 109,
            "column": "d_day",
            "min": "2026-01-01",
            "max": "2026-03-01",
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "jjj_number",
            "table_id": 110,
            "column": "fiscal_year",
            "min": 2019,
            "max": 2026,
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "kkk_priority",
            "table_id": 111,
            "column": "created_ts",
            "min": "2020-05-05T00:00:00Z",
            "max": "2026-02-02T00:00:00Z",
            "source": "fingerprint",
            "reason": None,
        },
        {
            "table": "lll_nullbounds",
            "table_id": 112,
            "column": "created",
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no fingerprint (use --sample)",
        },
    ]


def test_row_key_order_contract(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    assert list(result.json()[0]) == [
        "table",
        "table_id",
        "column",
        "min",
        "max",
        "source",
        "reason",
    ]


def test_single_metadata_request_for_whole_db(cli: Any, mock_api: Any):
    route = mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    assert len(result.json()) == 12
    assert route.call_count == 1  # one metadata GET covers every table


# -- time-column priority matrix ---------------------------------------------------------


def test_semantic_tie_breaks_on_lowest_database_position(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "aaa_semantic"
    ).assert_ok(0)
    row = result.json()[0]
    assert row["column"] == "updated_at"  # db_position 2 beats created_at's 3
    assert row["min"] == "2024-01-01T00:00:00Z"
    assert row["max"] == "2024-06-30T00:00:00Z"


def test_semantic_tier_beats_base_tier_even_at_worse_position(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "kkk_priority"
    ).assert_ok(0)
    assert result.json()[0]["column"] == "created_ts"  # db_position 9, still wins


def test_base_type_tier_beats_name_regex(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "bbb_base"
    ).assert_ok(0)
    assert result.json()[0]["column"] == "trade_time"  # not snap_date (db_position 2)


def test_no_time_column_row(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "ddd_notime"
    ).assert_ok(0)
    assert result.json() == [
        {
            "table": "ddd_notime",
            "table_id": 104,
            "column": None,
            "min": None,
            "max": None,
            "source": "none",
            "reason": "no time column",
        }
    ]


def _solo_meta(name: str) -> dict:
    return {**METADATA, "tables": [tentry(5001, "solo", [fcol(9001, name, db_position=1)])]}


@pytest.mark.parametrize(
    "name",
    ["date", "trade_dt", "d_day", "sale_month", "fiscal_year", "login_time", "OP_DATE"],
)
def test_name_regex_hits(cli: Any, mock_api: Any, name: str):
    mock_metadata(mock_api, _solo_meta(name))
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    row = result.json()[0]
    assert row["column"] == name
    assert row["source"] == "none"
    assert row["reason"] == "no fingerprint (use --sample)"


@pytest.mark.parametrize(
    "name", ["date_str", "datetime_val", "monthday", "dated", "time_series", "dtcode"]
)
def test_name_regex_misses_mean_no_time_column(cli: Any, mock_api: Any, name: str):
    mock_metadata(mock_api, _solo_meta(name))
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    row = result.json()[0]
    assert row["column"] is None
    assert row["reason"] == "no time column"


# -- fingerprint shape variants ----------------------------------------------------------


def test_fingerprint_null_means_no_fingerprint(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "fff_nullfp"
    ).assert_ok(0)
    row = result.json()[0]
    assert row["source"] == "none"
    assert row["reason"] == "no fingerprint (use --sample)"
    assert row["column"] == "created"


def test_fingerprint_key_absent_same_as_null(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "ggg_missingfp"
    ).assert_ok(0)
    assert result.json()[0]["reason"] == "no fingerprint (use --sample)"


def test_global_only_fingerprint_has_no_bounds(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "hhh_reserved"
    ).assert_ok(0)
    assert result.json()[0]["reason"] == "no fingerprint (use --sample)"


def test_temporal_fingerprint_with_null_bounds_is_unusable(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "lll_nullbounds"
    ).assert_ok(0)
    assert result.json()[0]["reason"] == "no fingerprint (use --sample)"


def test_number_fingerprint_min_max_used(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "jjj_number"
    ).assert_ok(0)
    row = result.json()[0]
    assert (row["source"], row["min"], row["max"]) == ("fingerprint", 2019, 2026)


def test_unit_fingerprint_bounds_nested_min_max_keys():
    field = {"fingerprint": {"type": {"type/DateTime": {"min": "a", "max": "b"}}}}
    assert _fingerprint_bounds(field) == ("a", "b")


def test_unit_fingerprint_bounds_absent_and_flat_shapes():
    assert _fingerprint_bounds({}) == (None, None)
    assert _fingerprint_bounds({"fingerprint": None}) == (None, None)
    assert _fingerprint_bounds({"fingerprint": {"global": {"distinct-count": 3}}}) == (None, None)
    assert _fingerprint_bounds({"fingerprint": {"min": "2019-01-01"}}) == ("2019-01-01", None)


# -- zero-query proof (the ADR #19 core) ---------------------------------------------------


def test_zero_dataset_queries_without_sample(cli: Any, mock_api: Any):
    meta_route = mock_metadata(mock_api)
    dataset_route = mock_dataset(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    assert len(result.json()) == 12
    assert dataset_route.call_count == 0  # fingerprint mode: ZERO dataset queries
    assert meta_route.call_count == 1


# -- --sample path ------------------------------------------------------------------------


def test_sample_backticked_sql_exact(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    route = mock_dataset(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "fff_nullfp",
        "--sample",
    ).assert_ok(0)
    row = result.json()[0]
    assert row == {
        "table": "fff_nullfp",
        "table_id": 106,
        "column": "created",
        "min": "2019-01-01",
        "max": "2026-08-01",
        "source": "sample",
        "reason": None,
    }
    assert route.call_count == 1
    assert request_sql(route.calls[0]) == "SELECT MIN(`created`), MAX(`created`) FROM `fff_nullfp`"


def test_sample_reserved_word_column_is_backticked(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    route = mock_dataset(mock_api)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "hhh_reserved",
        "--sample",
    ).assert_ok(0)
    assert request_sql(route.calls[0]) == (
        "SELECT MIN(`year_month`), MAX(`year_month`) FROM `hhh_reserved`"
    )


def test_sample_skips_tables_with_fingerprint(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    route = mock_dataset(mock_api)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--sample",
    ).assert_ok(0)
    assert route.call_count == 0  # fingerprint available -> no sample query


def test_sample_queries_only_fingerprint_less_tables_by_default(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    route = mock_dataset(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--sample").assert_ok(
        0
    )
    sqls = {request_sql(call) for call in route.calls}
    # exactly the 5 tables without usable fingerprints: ccc/fff/ggg/hhh/lll
    assert sqls == {
        "SELECT MIN(`report_date`), MAX(`report_date`) FROM `ccc_nameregex`",
        "SELECT MIN(`created`), MAX(`created`) FROM `fff_nullfp`",
        "SELECT MIN(`login_time`), MAX(`login_time`) FROM `ggg_missingfp`",
        "SELECT MIN(`year_month`), MAX(`year_month`) FROM `hhh_reserved`",
        "SELECT MIN(`created`), MAX(`created`) FROM `lll_nullbounds`",
    }
    sampled = {row["table"] for row in result.json() if row["source"] == "sample"}
    assert sampled == {
        "ccc_nameregex",
        "fff_nullfp",
        "ggg_missingfp",
        "hhh_reserved",
        "lll_nullbounds",
    }


def test_sample_values_come_from_dataset_rows(cli: Any, mock_api: Any):
    payload = {
        **METADATA,
        "tables": [
            tentry(
                105, "eee_flat2", [fcol(11, "dt", "type/DateTime", db_position=1, fingerprint=None)]
            )
        ],
    }
    mock_metadata(mock_api, payload)
    mock_dataset(
        mock_api,
        rows_by_sql={"SELECT MIN(`dt`), MAX(`dt`) FROM `eee_flat2`": [["2020-02-02", None]]},
    )
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat2",
        "--sample",
    ).assert_ok(0)
    row = result.json()[0]
    assert (row["source"], row["min"], row["max"]) == ("sample", "2020-02-02", None)


def test_sample_empty_result_yields_null_bounds(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    mock_dataset(
        mock_api, rows_by_sql={"SELECT MIN(`created`), MAX(`created`) FROM `fff_nullfp`": []}
    )
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "fff_nullfp",
        "--sample",
    ).assert_ok(0)
    row = result.json()[0]
    assert row["source"] == "sample"
    assert (row["min"], row["max"]) == (None, None)


def test_sample_dataset_error_propagates_bad_query(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400, json={"error": "boom", "status": "failed", "error_type": "invalid"}
        )
    )
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "fff_nullfp",
        "--sample",
    )
    assert result.exit_code == 1
    assert result.error()["code"] == "bad_query"


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ({"name": "t", "schema": ""}, "created", "SELECT MIN(`created`), MAX(`created`) FROM `t`"),
        (
            {"name": "events", "schema": "reports"},
            "d",
            "SELECT MIN(`d`), MAX(`d`) FROM `reports`.`events`",
        ),
        (
            {"name": "t", "schema": None},
            "we`ird",
            "SELECT MIN(`we``ird`), MAX(`we``ird`) FROM `t`",
        ),
    ],
)
def test_unit_sample_sql(table: dict, column: str, expected: str):
    assert _sample_sql(table, {"name": column}) == expected


def test_unit_quote_ident_doubles_backticks():
    assert _quote_ident("a`b") == "`a``b`"


# -- table selection ---------------------------------------------------------------------


def test_table_by_numeric_id(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "105"
    ).assert_ok(0)
    assert [row["table"] for row in result.json()] == ["eee_flat"]


def test_table_by_name(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "eee_flat"
    ).assert_ok(0)
    assert [row["table"] for row in result.json()] == ["eee_flat"]


def test_table_not_found(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "nope")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "not_found"
    assert error["detail"]["kind"] == "table"


def test_table_ambiguous_ref_lists_candidates(cli: Any, mock_api: Any):
    payload = {
        **METADATA,
        "tables": [
            tentry(1, "dup", [fcol(1, "date", "type/DateTime", db_position=1)]),
            tentry(2, "dup", [fcol(2, "date", "type/DateTime", db_position=1)]),
        ],
    }
    mock_metadata(mock_api, payload)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--table", "dup")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "ambiguous_ref"
    assert sorted(c["id"] for c in error["detail"]["candidates"]) == [1, 2]


def test_table_order_preserved_as_given(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "jjj_number",
        "--table",
        "aaa_semantic",
    ).assert_ok(0)
    assert [row["table"] for row in result.json()] == ["jjj_number", "aaa_semantic"]


def test_max_tables_alphabetical_cutoff_json_envelope(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url", BASE, "--api-key", "k", "freshness", "--db", "15", "--max-tables", "3"
    ).assert_ok(0)
    payload = result.json()
    assert list(payload) == ["truncated", "rows_returned", "items"]
    assert payload["truncated"] is True
    assert payload["rows_returned"] == 3
    assert [row["table"] for row in payload["items"]] == [
        "aaa_semantic",
        "bbb_base",
        "ccc_nameregex",
    ]


def test_max_tables_csv_truncation_note(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--max-tables",
        "3",
        "--format",
        "csv",
    ).assert_ok(0)
    assert result.stdout.splitlines()[0] == "table,table_id,column,min,max,source,reason"
    assert result.stdout.endswith('# {"truncated": true, "rows_returned": 3}\n')


def test_no_truncation_when_under_cap(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "15").assert_ok(0)
    payload = result.json()
    assert isinstance(payload, list)  # plain array, no envelope
    assert len(payload) == 12


def test_named_tables_bypass_the_cap(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--max-tables",
        "1",
        "--table",
        "jjj_number",
        "--table",
        "aaa_semantic",
    ).assert_ok(0)
    payload = result.json()
    assert isinstance(payload, list)
    assert [row["table"] for row in payload] == ["jjj_number", "aaa_semantic"]


# -- unit: deterministic pick details ------------------------------------------------------


def test_unit_tie_same_position_keeps_metadata_order():
    fields = [
        fcol(1, "start_date", "type/DateTime", db_position=2),
        fcol(2, "end_date", "type/DateTime", db_position=2),
    ]
    assert _pick_time_column(fields)["name"] == "start_date"


def test_unit_position_falls_back_to_ui_position():
    fields = [
        fcol(1, "a_time", position=5),  # database_position missing -> UI position
        fcol(2, "b_time", position=1),
    ]
    assert _pick_time_column(fields)["name"] == "b_time"


def test_unit_no_candidates_returns_none():
    assert _pick_time_column([fcol(1, "value", "type/Float")]) is None
    assert _pick_time_column([]) is None


# -- five-format rendering & plumbing ------------------------------------------------------


def test_format_csv(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--format",
        "csv",
    ).assert_ok(0)
    assert result.stdout == (
        "table,table_id,column,min,max,source,reason\r\n"
        "eee_flat,105,dt,2019-01-01,2026-08-01,fingerprint,\r\n"
    )


def test_format_ndjson_one_record_per_line(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--table",
        "ddd_notime",
        "--format",
        "ndjson",
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert len(lines) == 2  # no sentinel when not truncated
    first, second = (json.loads(line) for line in lines)
    assert first["column"] == "dt"
    assert second["reason"] == "no time column"


def test_format_table(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--format",
        "table",
    ).assert_ok(0)
    assert "table_id" in result.stdout
    assert "eee_flat" in result.stdout
    assert "2026-08-01" in result.stdout


def test_format_markdown(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--format",
        "markdown",
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert lines[0] == "| table | table_id | column | min | max | source | reason |"
    assert lines[1] == "|---|---|---|---|---|---|---|"
    assert lines[2] == "| eee_flat | 105 | dt | 2019-01-01 | 2026-08-01 | fingerprint |  |"


def test_fields_pruning(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--fields",
        "table,min,max",
    ).assert_ok(0)
    assert result.json() == [{"table": "eee_flat", "min": "2019-01-01", "max": "2026-08-01"}]


def test_fields_unknown_is_invalid_args(cli: Any, mock_api: Any):
    mock_metadata(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--fields",
        "table,bogus",
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_out_writes_file_with_stdout_summary(cli: Any, mock_api: Any, tmp_path: Any):
    mock_metadata(mock_api)
    target = tmp_path / "fresh.json"
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "freshness",
        "--db",
        "15",
        "--table",
        "eee_flat",
        "--out",
        str(target),
    ).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 1, "truncated": False}
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written[0]["table"] == "eee_flat"
    assert written[0]["source"] == "fingerprint"


# -- db resolution -------------------------------------------------------------------------


def test_db_by_name(cli: Any, mock_api: Any):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_FOR_NAME)
    )
    route = mock_metadata(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "freshness", "--db", "quant_data").assert_ok(0)
    assert len(result.json()) == 12
    assert route.call_count == 1


def test_missing_db_is_invalid_args_exit_2(cli: Any, mock_api: Any):
    result = cli("--url", BASE, "--api-key", "k", "freshness")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"
