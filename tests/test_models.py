"""Model whitelist freezing (DESIGN §5): unknown upstream fields are dropped."""

from __future__ import annotations

import json

from mbcli.models import (
    CardDetail,
    CollectionInfo,
    DashboardDetail,
    DatabaseInfo,
    FieldInfo,
    SearchItem,
    TableInfo,
    WhoamiInfo,
)


def test_database_info_drops_unknown_keys():
    info = DatabaseInfo.from_api(
        {
            "id": 7,
            "name": "bi_data",
            "engine": "mysql",
            "description": None,
            "is_sample": False,
            "initial_sync_status": "complete",
            "timezone": "Asia/Shanghai",
            "dbms_version": "8.0.33",
            "native_permissions": ["none"],  # unknown upstream field
            "features": ["basic-aggregations"],
        }
    )
    assert set(info.to_dict()) == {
        "id",
        "name",
        "engine",
        "description",
        "is_sample",
        "initial_sync_status",
        "timezone",
        "dbms_version",
    }


def test_whoami_info_whitelist():
    me = WhoamiInfo.from_api(
        {
            "id": 1,
            "email": "a@b.c",
            "is_superuser": False,
            "group_ids": [1, 10],
            "first_name": "Z",
            "last_name": "H",
            "locale": "zh",
        }
    )
    assert me.to_dict() == {"id": 1, "email": "a@b.c", "is_superuser": False, "group_ids": [1, 10]}


def test_table_info_counts_fields_and_defaults():
    table = TableInfo.from_api(
        {
            "id": 3258,
            "schema": "",
            "name": "app_mau",
            "display_name": "app_mau",
            "estimated_row_count": 1000000000,
            "fields": [{"id": 1}, {"id": 2}, {"id": 3}],
        }
    )
    assert table.field_count == 3
    assert table.active is True
    assert table.description is None


def test_field_info_hydrates_fk_target():
    field = FieldInfo.from_api(
        {
            "id": 37677,
            "name": "uid",
            "display_name": "uid",
            "base_type": "type/BigInteger",
            "semantic_type": "type/FK",
            "database_type": "BIGINT",
            "database_is_pk": False,
            "database_is_nullable": False,
            "database_indexed": True,
            "position": 2,
            "target": {"id": 11, "name": "id", "table": {"id": 5, "name": "users"}},
        }
    )
    assert field.fk_target is not None
    assert field.fk_target.to_dict() == {"table": "users", "field": "id"}
    assert field.is_pk is False
    assert field.is_indexed is True


def test_field_info_without_target_has_null_fk():
    field = FieldInfo.from_api({"id": 1, "name": "x"})
    assert field.fk_target is None


def test_card_detail_native_sql_extracted():
    card = CardDetail.from_api(
        {
            "id": 7876,
            "name": "数据日期",
            "dataset_query": {"database": 13, "type": "native", "native": {"query": "SELECT 1"}},
            "collection_id": 1039,
            "display": "scalar",
            "parameters": [],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-06-01T00:00:00Z",
            "last-edit-info": {"first_name": "Z", "last_name": "H", "timestamp": "2024-06-01"},
        }
    )
    data = card.to_dict()
    assert data["query_type"] == "native"
    assert data["sql"] == "SELECT 1"
    assert data["mbql"] is None
    assert data["database_id"] == 13
    assert data["last_edit"] == {"user": "Z H", "at": "2024-06-01"}
    assert data["collection"] == {"id": 1039, "name": None}


def test_card_detail_mbql_path():
    card = CardDetail.from_api(
        {
            "id": 1,
            "dataset_query": {
                "database": 2,
                "type": "query",
                "query": {"source-table": 5, "aggregation": [["count"]]},
            },
        }
    )
    assert card.query_type == "mbql"
    assert card.sql is None
    assert card.mbql == {"source-table": 5, "aggregation": [["count"]]}


def test_card_detail_pmbql_native_sql_extracted():
    # Real payload shape from card 7876 on instance v0.66 (probe 2026-10-30)
    card = CardDetail.from_api(
        {
            "id": 7876,
            "name": "数据日期",
            "dataset_query": {
                "lib/type": "mbql/query",
                "stages": [
                    {
                        "lib/type": "mbql.stage/native",
                        "native": "select max(date) as max_date from tab_net_moblie_user_month",
                    }
                ],
                "database": 13,
            },
            "collection_id": 1039,
            "display": "scalar",
        }
    )
    data = card.to_dict()
    assert data["query_type"] == "native"
    assert data["sql"] == "select max(date) as max_date from tab_net_moblie_user_month"
    assert data["mbql"] is None
    assert data["database_id"] == 13


def test_card_detail_pmbql_last_native_stage_wins():
    card = CardDetail.from_api(
        {
            "id": 2,
            "dataset_query": {
                "lib/type": "mbql/query",
                "stages": [
                    {"lib/type": "mbql.stage/native", "native": "SELECT first"},
                    {"lib/type": "mbql.stage/native", "native": "SELECT last"},
                ],
                "database": 5,
            },
        }
    )
    assert card.sql == "SELECT last"


def test_card_detail_pmbql_mbql_passthrough():
    pmbql = {
        "lib/type": "mbql/query",
        "stages": [{"lib/type": "mbql.stage/mbql", "source-table": 5, "aggregation": [["count"]]}],
        "database": 2,
    }
    card = CardDetail.from_api({"id": 3, "dataset_query": pmbql})
    assert card.query_type == "mbql"
    assert card.sql is None
    assert card.mbql == pmbql
    assert card.database_id == 2


def test_card_detail_legacy_query_string_fallback():
    # dataset_query unparseable → fall back to JSON-encoded legacy_query (v57+)
    card = CardDetail.from_api(
        {
            "id": 4,
            "dataset_query": {},
            "legacy_query": json.dumps(
                {"database": 13, "type": "native", "native": {"query": "SELECT 9"}}
            ),
        }
    )
    assert card.query_type == "native"
    assert card.sql == "SELECT 9"
    assert card.database_id == 13


def test_card_detail_legacy_query_garbage_string_stays_null():
    card = CardDetail.from_api({"id": 5, "dataset_query": {}, "legacy_query": "{not json"})
    assert card.query_type is None
    assert card.sql is None
    assert card.mbql is None


def test_field_info_position_prefers_database_position():
    field = FieldInfo.from_api({"id": 1, "name": "b", "database_position": 7, "position": 99})
    assert field.position == 7
    # legacy payload without database_position falls back to UI position
    fallback = FieldInfo.from_api({"id": 2, "name": "a", "position": 3})
    assert fallback.position == 3


def test_dashboard_detail_structure():
    dashboard = DashboardDetail.from_api(
        {
            "id": 1607,
            "name": "ops",
            "description": None,
            "collection": {"id": 9, "name": "c"},
            "tabs": [{"id": 1, "name": "Tab 1"}],
            "parameters": [
                {
                    "id": "p1",
                    "name": "Date",
                    "slug": "date",
                    "type": "date/all-options",
                    "default": "2024-01-01",
                    "extra_field": "dropped",
                }
            ],
            "dashcards": [
                {
                    "card": {"id": 12, "name": "MAU"},
                    "series": [{"id": 3, "name": "extra"}],
                    "row": 0,
                    "col": 0,
                    "size_x": 4,
                    "size_y": 4,
                    "parameter_mappings": [{"parameter_id": "p1"}],
                },
                {
                    "card": {"id": 13, "name": "Revenue"},
                    "series": [],
                    "row": 0,
                    "col": 4,
                    "size_x": 8,
                    "size_y": 4,
                    "parameter_mappings": [],
                },
            ],
        }
    )
    data = dashboard.to_dict()
    assert data["tabs"] == [{"id": 1, "name": "Tab 1"}]
    assert data["parameters"][0] == {
        "id": "p1",
        "name": "Date",
        "slug": "date",
        "type": "date/all-options",
        "default": "2024-01-01",
    }
    assert data["cards"][0]["card_id"] == 12
    assert data["cards"][0]["series"] == ["extra"]
    assert data["cards"][0]["position"] == {"row": 0, "col": 0, "size_x": 4, "size_y": 4}
    assert data["cards"][0]["parameter_mappings"] == [{"parameter_id": "p1"}]
    assert data["cards"][1]["series"] == []


def test_collection_info_and_search_item():
    collection = CollectionInfo.from_api(
        {
            "id": 663,
            "name": "共享报表",
            "parent_id": None,
            "authority_level": None,
            "is_personal": False,
            "slug": "extra",
        }
    )
    assert collection.to_dict() == {
        "id": 663,
        "name": "共享报表",
        "parent_id": None,
        "authority_level": None,
        "is_personal": False,
    }
    item = SearchItem.from_api(
        {
            "id": 7876,
            "model": "card",
            "name": "数据日期",
            "description": None,
            "collection": {"id": 1039, "name": "量化"},
            "database_id": 13,
            "updated_at": "2024-06-01T00:00:00Z",
            "score": 12.5,
        }
    )
    assert item.to_dict() == {
        "id": 7876,
        "model": "card",
        "name": "数据日期",
        "description": None,
        "collection": {"id": 1039, "name": "量化"},
        "database_id": 13,
        "updated_at": "2024-06-01T00:00:00Z",
    }


def test_query_envelope_json_roundtrip_is_stable():
    from mbcli.models import QueryEnvelope

    payload = {
        "status": "completed",
        "data": {
            "cols": [
                {
                    "name": "one",
                    "base_type": "type/BigInteger",
                    "database_type": "BIGINT",
                    "extra": "x",
                }
            ],
            "rows": [[1], ["x,y"], [None]],
        },
        "row_count": 3,
        "running_time": 42,
        "cached": True,
    }
    envelope = QueryEnvelope.from_response(payload)
    rendered = json.dumps(envelope.to_dict(), ensure_ascii=False)
    parsed = json.loads(rendered)
    assert parsed["columns"] == [
        {"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"}
    ]
    assert parsed["rows"] == [[1], ["x,y"], [None]]
    assert parsed["running_time_ms"] == 42
    assert parsed["cached"] is True
