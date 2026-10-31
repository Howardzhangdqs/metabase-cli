"""Golden/contract tests for M3 asset commands: collections, dashboards, search.

Canned payloads mirror the real probe shapes (DESIGN §0): the collection items
endpoint returns {total,data,models,limit,offset} with a `model` field per item;
dashboards carry dashcards[] with card/series/col/row/size_x/size_y/
parameter_mappings; search returns {data,total,limit,offset,models,…}.
Offline only (respx).
"""

from __future__ import annotations

import httpx

BASE = "http://test.local"

# -- canned payloads (real probe shapes, trimmed but representative) ------------


COLLECTIONS_PAYLOAD = [
    {
        "id": "root",
        "name": "Our analytics",
        "description": None,
        "parent_id": None,
        "authority_level": None,
        "is_personal": False,
        "location": "/",
        "namespace": None,
        "personal_collection_id": None,
    },
    {
        "id": 663,
        "name": "共享报表",
        "description": "全组共享",
        "parent_id": None,
        "authority_level": None,
        "is_personal": False,
        "location": "/",
        "unknown_extra": "dropped",
    },
    {
        "id": 700,
        "name": "John 的个人集合",
        "description": None,
        "parent_id": None,
        "authority_level": None,
        "is_personal": True,
    },
    {
        "id": 701,
        "name": "Jane 的个人集合",
        "description": None,
        "parent_id": 663,
        "authority_level": 1,
        "is_personal": True,
    },
]

COLLECTION_ITEMS_PAYLOAD = {
    "total": 2,
    "data": [
        {
            "id": 7876,
            "model": "card",
            "name": "数据日期",
            "description": None,
            "collection": {"id": 1039, "name": "报表", "is_personal": False},
            "display": "scalar",
            "updated_at": "2026-10-01T08:00:00.000Z",
            "extra_field": "dropped",
        },
        {
            "id": 1607,
            "model": "dashboard",
            "name": "运营看板",
            "description": "核心指标",
            "collection": {"id": 1039, "name": "报表"},
            "display": None,
            "updated_at": "2026-10-02T08:00:00.000Z",
        },
    ],
    "models": ["card", "dashboard"],
    "limit": 20,
    "offset": 0,
}

COLLECTION_ITEMS_EXPECTED = {
    "total": 2,
    "limit": 20,
    "offset": 0,
    "items": [
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
            "description": "核心指标",
            "collection": {"id": 1039, "name": "报表"},
            "display": None,
            "updated_at": "2026-10-02T08:00:00.000Z",
        },
    ],
}

DASHBOARD_PAYLOAD = {
    "id": 1607,
    "name": "运营看板",
    "description": None,
    "collection": {"id": 1039, "name": "报表", "is_personal": False},
    "tabs": [{"id": 1, "name": "数据总览"}, {"id": 2, "name": "明细"}],
    "parameters": [
        {
            "id": "p1",
            "name": "日期",
            "slug": "date",
            "type": "date/all-options",
            "default": None,
            "values_source_type": None,
        }
    ],
    "dashcards": [
        {
            "id": 9001,
            "dashboard_id": 1607,
            "card_id": 12,
            "card": {
                "id": 12,
                "name": "销量趋势",
                "display": "line",
                "dataset_query": {"database": 13, "type": "query"},
            },
            "series": [{"id": 13, "name": "对比线"}],
            "col": 0,
            "row": 0,
            "size_x": 4,
            "size_y": 4,
            "parameter_mappings": [
                {
                    "parameter_id": "p1",
                    "card_id": 12,
                    "target": ["dimension", ["template-tag", "date"]],
                }
            ],
        },
        {
            "id": 9002,
            "dashboard_id": 1607,
            "card_id": 14,
            "card": {"id": 14, "name": "总计", "display": "scalar"},
            "series": [],
            "col": 4,
            "row": 0,
            "size_x": 2,
            "size_y": 2,
            "parameter_mappings": [],
        },
    ],
    "created_at": "2026-09-01T00:00:00.000Z",
    "updated_at": "2026-10-02T08:00:00.000Z",
    "unknown_top": "dropped",
}

DASHBOARD_EXPECTED = {
    "id": 1607,
    "name": "运营看板",
    "description": None,
    "collection": {"id": 1039, "name": "报表"},
    "tabs": [{"id": 1, "name": "数据总览"}, {"id": 2, "name": "明细"}],
    "parameters": [
        {"id": "p1", "name": "日期", "slug": "date", "type": "date/all-options", "default": None}
    ],
    "cards": [
        {
            "card_id": 12,
            "name": "销量趋势",
            "series": ["对比线"],
            "position": {"row": 0, "col": 0, "size_x": 4, "size_y": 4},
            "parameter_mappings": [
                {
                    "parameter_id": "p1",
                    "card_id": 12,
                    "target": ["dimension", ["template-tag", "date"]],
                }
            ],
        },
        {
            "card_id": 14,
            "name": "总计",
            "series": [],
            "position": {"row": 0, "col": 4, "size_x": 2, "size_y": 2},
            "parameter_mappings": [],
        },
    ],
}

SEARCH_PAYLOAD = {
    "data": [
        {
            "id": 7876,
            "model": "card",
            "name": "数据日期",
            "description": None,
            "collection": {"id": 1039, "name": "报表"},
            "database_id": 13,
            "updated_at": "2026-10-01T08:00:00.000Z",
            "extra": "dropped",
        },
        {
            "id": 3258,
            "model": "table",
            "name": "app_mau",
            "description": "日活",
            "collection": None,
            "database_id": None,
            "table_db_id": 15,
            "display": "table",
            "updated_at": "2026-10-01T08:00:00.000Z",
        },
        {
            "id": 1607,
            "model": "dashboard",
            "name": "其他看板",
            "description": None,
            "collection": {"id": 2, "name": "root"},
            "updated_at": "2026-10-01T08:00:00.000Z",
        },
    ],
    "total": 3,
    "limit": 20,
    "offset": 0,
    "models": ["card", "table", "dashboard"],
    "available_models": ["card", "collection", "dashboard", "dataset", "table"],
}

SEARCH_EXPECTED = [
    {
        "id": 7876,
        "model": "card",
        "name": "数据日期",
        "description": None,
        "collection": {"id": 1039, "name": "报表"},
        "database_id": 13,
        "database_name": "bi_data",
        "updated_at": "2026-10-01T08:00:00.000Z",
    },
    {
        "id": 3258,
        "model": "table",
        "name": "app_mau",
        "description": "日活",
        "collection": None,
        "database_id": None,
        "database_name": "quant_data",  # P2-1: tables resolve via table_db_id
        "updated_at": "2026-10-01T08:00:00.000Z",
    },
    {
        "id": 1607,
        "model": "dashboard",
        "name": "其他看板",
        "description": None,
        "collection": {"id": 2, "name": "root"},
        "database_id": None,
        "database_name": None,  # dashboards belong to no database
        "updated_at": "2026-10-01T08:00:00.000Z",
    },
]

DATABASES_FOR_NAME = {
    "data": [
        {"id": 13, "name": "bi_data"},
        {"id": 15, "name": "quant_data"},
    ],
    "total": 2,
}


# -- helpers ----------------------------------------------------------------------


def mock_route(mock_api, method_url: str, payload=None, **kwargs):
    route = mock_api.get(method_url)
    route.mock(return_value=httpx.Response(200, json=payload, **kwargs))
    return route


def mock_search_results(mock_api, payload=SEARCH_PAYLOAD):
    """Search route + the /api/database index P2-1 name decoration needs
    (SEARCH_PAYLOAD items reference database ids 13/15 → Resolver loads the
    cached id→name map: one extra request, zero per-item)."""
    mock_route(mock_api, f"{BASE}/api/database", DATABASES_FOR_NAME)
    return mock_route(mock_api, f"{BASE}/api/search", payload)


def last_params(route) -> httpx.QueryParams:
    return route.calls.last.request.url.params


# -- collections list ---------------------------------------------------------------


def test_collections_list_filters_personal_by_default(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "list").assert_ok(0)
    names = [item["name"] for item in result.json()]
    assert names == ["Our analytics", "共享报表"]


def test_collections_list_all_includes_personal(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "list", "--all").assert_ok(0)
    assert [item["name"] for item in result.json()] == [
        "Our analytics",
        "共享报表",
        "John 的个人集合",
        "Jane 的个人集合",
    ]


def test_collections_list_whitelist_and_root_id_string(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "list").assert_ok(0)
    parsed = result.json()
    assert all(
        list(item) == ["id", "name", "parent_id", "authority_level", "is_personal"]
        for item in parsed
    )
    assert parsed[0]["id"] == "root"  # root keeps its string id
    assert parsed[1] == {
        "id": 663,
        "name": "共享报表",
        "parent_id": None,
        "authority_level": None,
        "is_personal": False,
    }


def test_collections_list_fields_pruning(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "collections", "list", "--fields", "id,is_personal"
    ).assert_ok(0)
    assert result.json() == [
        {"id": "root", "is_personal": False},
        {"id": 663, "is_personal": False},
    ]


# -- collections show ----------------------------------------------------------------


def test_collections_show_sends_plural_models_param(cli, mock_api):
    route = mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "collections",
        "show",
        "1039",
        "--model",
        "card",
        "--model",
        "dashboard",
    ).assert_ok(0)
    params = last_params(route)
    # the filter MUST go out as repeated plural `models`; singular `model=` is
    # silently ignored by the server (probe-verified, ADR #10)
    assert params.get_list("models") == ["card", "dashboard"]
    assert "model" not in params


def test_collections_show_comma_separated_models(cli, mock_api):
    route = mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "collections",
        "show",
        "1039",
        "--model",
        "card, dashboard",
    ).assert_ok(0)
    assert last_params(route).get_list("models") == ["card", "dashboard"]


def test_collections_show_no_models_param_when_filter_absent(cli, mock_api):
    route = mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    cli("--url", BASE, "--api-key", "k", "collections", "show", "1039").assert_ok(0)
    assert "models" not in last_params(route)


def test_collections_show_root_uses_string_root_without_lookup(cli, mock_api):
    lookup = mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    route = mock_route(mock_api, f"{BASE}/api/collection/root/items", COLLECTION_ITEMS_PAYLOAD)
    cli("--url", BASE, "--api-key", "k", "collections", "show", "root").assert_ok(0)
    assert lookup.call_count == 0  # reserved word: no /api/collection listing fetch
    assert route.call_count == 1


def test_collections_show_resolves_collection_by_name(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    route = mock_route(mock_api, f"{BASE}/api/collection/663/items", COLLECTION_ITEMS_PAYLOAD)
    cli("--url", BASE, "--api-key", "k", "collections", "show", "共享报表").assert_ok(0)
    assert route.call_count == 1


def test_collections_show_unknown_collection_not_found(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection", COLLECTIONS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "show", "不存在")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_collections_show_limit_offset_passthrough(cli, mock_api):
    route = mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "collections",
        "show",
        "1039",
        "--limit",
        "5",
        "--offset",
        "10",
    ).assert_ok(0)
    params = last_params(route)
    assert params.get("limit") == "5"
    assert params.get("offset") == "10"


def test_collections_show_envelope_golden(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "collections", "show", "1039").assert_ok(0)
    assert result.json() == COLLECTION_ITEMS_EXPECTED
    assert list(result.json()) == ["total", "limit", "offset", "items"]


def test_collections_show_fields_prunes_items_not_envelope(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "collections",
        "show",
        "1039",
        "--fields",
        "id,model,name",
    ).assert_ok(0)
    parsed = result.json()
    assert list(parsed) == ["total", "limit", "offset", "items"]  # envelope intact
    assert parsed["items"] == [
        {"id": 7876, "model": "card", "name": "数据日期"},
        {"id": 1607, "model": "dashboard", "name": "运营看板"},
    ]


def test_collections_show_unknown_fields_exit_2(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/collection/1039/items", COLLECTION_ITEMS_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "collections", "show", "1039", "--fields", "id,nope"
    )
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


# -- dashboards show -------------------------------------------------------------------


def test_dashboards_show_golden(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/dashboard/1607", DASHBOARD_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "dashboards", "show", "1607").assert_ok(0)
    assert result.json() == DASHBOARD_EXPECTED
    assert list(result.json()) == [
        "id",
        "name",
        "description",
        "collection",
        "tabs",
        "parameters",
        "cards",
    ]


def test_dashboards_show_trims_dashcard_noise(cli, mock_api):
    """120-dashcard dashboards must come back trimmed to the §5.3 whitelist."""
    mock_route(mock_api, f"{BASE}/api/dashboard/1607", DASHBOARD_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "dashboards", "show", "1607").assert_ok(0)
    card = result.json()["cards"][0]
    assert list(card) == ["card_id", "name", "series", "position", "parameter_mappings"]
    assert "dataset_query" not in result.stdout
    assert "unknown_top" not in result.stdout
    assert "dashboard_id" not in result.stdout


def test_dashboards_show_404_plain_text(cli, mock_api):
    mock_api.get(f"{BASE}/api/dashboard/999999").mock(
        return_value=httpx.Response(404, text="Not found.")
    )
    result = cli("--url", BASE, "--api-key", "k", "dashboards", "show", "999999")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"


def test_dashboards_show_fields_prunes_detail(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/dashboard/1607", DASHBOARD_PAYLOAD)
    result = cli(
        "--url", BASE, "--api-key", "k", "dashboards", "show", "1607", "--fields", "id,cards"
    ).assert_ok(0)
    parsed = result.json()
    assert list(parsed) == ["id", "cards"]
    assert parsed["id"] == 1607


def test_dashboards_show_table_format_smoke(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/dashboard/1607", DASHBOARD_PAYLOAD)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "--no-color",
        "dashboards",
        "show",
        "1607",
        "--format",
        "table",
    ).assert_ok(0)
    assert "id" in result.stdout and "运营看板" in result.stdout


# -- search ----------------------------------------------------------------------------


def test_search_sends_q_and_plural_models(cli, mock_api):
    route = mock_search_results(mock_api)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "search",
        "数据",
        "--model",
        "card",
        "--model",
        "dashboard",
    ).assert_ok(0)
    params = last_params(route)
    assert params.get("q") == "数据"
    assert params.get_list("models") == ["card", "dashboard"]
    assert "model" not in params


def test_search_comma_models_limit_offset(cli, mock_api):
    route = mock_search_results(mock_api)
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "search",
        "数据",
        "--model",
        "card,table",
        "--limit",
        "20",
        "--offset",
        "40",
    ).assert_ok(0)
    params = last_params(route)
    assert params.get_list("models") == ["card", "table"]
    assert params.get("limit") == "20"
    assert params.get("offset") == "40"


def test_search_no_params_when_defaults(cli, mock_api):
    route = mock_search_results(mock_api)
    cli("--url", BASE, "--api-key", "k", "search", "数据").assert_ok(0)
    params = last_params(route)
    assert params.get("q") == "数据"
    assert "models" not in params
    assert "limit" not in params
    assert "offset" not in params


def test_search_whitelist_golden(cli, mock_api):
    mock_search_results(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据").assert_ok(0)
    assert result.json() == SEARCH_EXPECTED
    assert all(
        list(item)
        == [
            "id",
            "model",
            "name",
            "description",
            "collection",
            "database_id",
            "database_name",
            "updated_at",
        ]
        for item in result.json()
    )


def test_search_db_page_filter_drops_mismatch_keeps_match(cli, mock_api):
    """--db is a CLIENT-side filter on the returned page (DESIGN §4): cards are
    matched via database_id, table-model items via table_db_id, items with
    neither key are dropped."""
    mock_search_results(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--db", "15").assert_ok(0)
    assert result.json() == [SEARCH_EXPECTED[1]]  # only the table item (table_db_id=15)


def test_search_db_matches_card_database_id(cli, mock_api):
    mock_search_results(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--db", "13").assert_ok(0)
    assert result.json() == [SEARCH_EXPECTED[0]]  # the card (database_id=13)


def test_search_db_numeric_id_still_fetches_listing_for_names(cli, mock_api):
    """A numeric --db still resolves without a lookup, but since v0.2.2 the
    search output decorates items with database_name (P2-1), which loads the
    Resolver's cached /api/database index exactly once — shared with any
    --db-by-name resolution, never per item."""
    listing = mock_search_results(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--db", "15").assert_ok(0)
    assert listing.call_count == 1  # one cached index fetch for name decoration
    assert result.json() == [SEARCH_EXPECTED[1]]


def test_search_db_by_name_resolves_via_listing(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/database", DATABASES_FOR_NAME)
    mock_search_results(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--db", "quant_data").assert_ok(
        0
    )
    assert result.json() == [SEARCH_EXPECTED[1]]


def test_search_db_unknown_name_not_found(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/database", DATABASES_FOR_NAME)
    search_route = mock_route(mock_api, f"{BASE}/api/search", SEARCH_PAYLOAD)
    result = cli("--url", BASE, "--api-key", "k", "search", "数据", "--db", "nope_db")
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"
    assert search_route.call_count == 0  # fail fast: no search request


def test_search_empty_page(cli, mock_api):
    mock_route(mock_api, f"{BASE}/api/search", {**SEARCH_PAYLOAD, "data": [], "total": 0})
    result = cli("--url", BASE, "--api-key", "k", "search", "zzz").assert_ok(0)
    assert result.json() == []


def test_search_csv_and_fields(cli, mock_api):
    mock_search_results(mock_api)
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "search",
        "数据",
        "--format",
        "csv",
        "--fields",
        "id,model,name",
    ).assert_ok(0)
    lines = result.stdout.splitlines()
    assert lines[0] == "id,model,name"
    assert lines[1] == "7876,card,数据日期"
    assert lines[2] == "3258,table,app_mau"
