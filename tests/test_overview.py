"""overview command tests (DESIGN §13.2) — offline, respx canned payloads.

Canned shapes mirror the 2026-10-31 read-only probe of the live v0.66 instance:
- /api/database?include=tables wraps as {"data":[…],"total":N} with a tables[]
  per db (no fields); the fallback tests mock a rejected param / missing arrays.
- /api/collection is a flat list WITHOUT the root collection (synthesized here).
- items endpoints take REPEATED models= params; live items carry
  collection_id + last-edit-info instead of collection/updated_at.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

BASE = "http://test.local"

# -- canned payloads --------------------------------------------------------------


def _table(table_id: int, name: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": table_id, "name": name, "active": True, "schema": ""}
    payload.update(extra)
    return payload


DATABASES_INCLUDE_PAYLOAD = {
    "data": [
        {
            "id": 7,
            "name": "bi_data",
            "engine": "mysql",
            "tables": [_table(1, "t1"), _table(2, "t2")],
        },
        {
            "id": 15,
            "name": "quant_data",
            "engine": "mysql",
            "tables": [
                _table(3, "fut_a"),
                _table(4, "hidden_one", visibility_type="hidden"),
                _table(5, "inactive_one", active=False),
            ],
        },
        {"id": 2, "name": "sample", "engine": "h2", "tables": []},
    ],
    "total": 3,
}

# live shape: the flat list does NOT contain the root collection
COLLECTIONS_LIST = [
    {"id": 663, "name": "共享报表", "parent_id": None, "is_personal": False, "location": "/"},
    {"id": 900, "name": "子集合", "parent_id": 663, "is_personal": False, "location": "/663/"},
    {"id": 700, "name": "张三的个人集合", "parent_id": None, "is_personal": True},
    {"id": 701, "name": "李四的个人子集合", "parent_id": 663, "is_personal": True},
]

ITEMS: dict[str, list[dict[str, Any]]] = {
    # root: one item without updated_at (falls back to last-edit-info.timestamp,
    # probe-verified live shape) and one off-model item that must be skipped
    "root": [
        {
            "id": 101,
            "model": "card",
            "name": "卡A",
            "collection_id": "root",
            "updated_at": "2026-10-01T08:00:00Z",
        },
        {
            "id": 102,
            "model": "card",
            "name": "卡B",
            "collection_id": "root",
            "updated_at": "2026-10-05T08:00:00Z",
        },
        {
            "id": 103,
            "model": "card",
            "name": "卡C",
            "collection_id": "root",
            "last-edit-info": {"timestamp": "2026-10-07T09:00:00Z"},
        },
        {
            "id": 201,
            "model": "dashboard",
            "name": "板X",
            "collection_id": "root",
            "updated_at": "2026-10-03T08:00:00Z",
        },
        {
            "id": 999,
            "model": "pulse",
            "name": "通知",
            "collection_id": "root",
            "updated_at": "2026-10-09T00:00:00Z",
        },
    ],
    "663": [
        {
            "id": 104,
            "model": "card",
            "name": "卡D",
            "collection_id": 663,
            "updated_at": "2026-10-06T08:00:00Z",
        },
        {
            "id": 202,
            "model": "dashboard",
            "name": "板Y",
            "collection_id": 663,
            "updated_at": "2026-09-28T08:00:00Z",
        },
    ],
}

TOTALS: dict[tuple[str, str], int] = {
    ("root", "card"): 3,
    ("root", "dashboard"): 1,
    ("663", "card"): 2,
    ("663", "dashboard"): 2,
}


def _items_handler(coll: str, totals: dict[tuple[str, str], int], items: dict[str, Any]):
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        models = params.get_list("models")
        if params.get("limit") == "1":  # the 1-row total probe
            return httpx.Response(
                200,
                json={
                    "total": totals[(coll, models[0])],
                    "data": [],
                    "models": models,
                    "limit": 1,
                    "offset": 0,
                },
            )
        # the merged card+dashboard recents page
        return httpx.Response(
            200,
            json={
                "total": len(items[coll]),
                "data": items[coll],
                "models": models,
                "limit": 100,
                "offset": 0,
            },
        )

    return handler


def _mock_items(mock_api, totals=None, items=None) -> dict[str, Any]:
    totals = TOTALS if totals is None else totals
    items = ITEMS if items is None else items
    routes = {}
    for coll in {coll for coll, _model in totals}:
        routes[f"items:{coll}"] = mock_api.get(f"{BASE}/api/collection/{coll}/items").mock(
            side_effect=_items_handler(coll, totals, items)
        )
    return routes


def _mock_common(
    mock_api,
    *,
    databases_payload: Any = DATABASES_INCLUDE_PAYLOAD,
    collections: list[dict[str, Any]] | None = None,
    totals: dict[tuple[str, str], int] | None = None,
    items: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    routes: dict[str, Any] = {
        "databases": mock_api.get(f"{BASE}/api/database").mock(
            return_value=httpx.Response(200, json=databases_payload)
        )
    }
    routes["collections"] = mock_api.get(f"{BASE}/api/collection").mock(
        return_value=httpx.Response(
            200, json=COLLECTIONS_LIST if collections is None else collections
        )
    )
    routes.update(_mock_items(mock_api, totals, items))
    return routes


EXPECTED_RECENT = [
    {
        "model": "card",
        "id": 103,
        "name": "卡C",
        "collection": {"id": "root", "name": "Our analytics"},
        "updated_at": "2026-10-07T09:00:00Z",
    },
    {
        "model": "card",
        "id": 104,
        "name": "卡D",
        "collection": {"id": 663, "name": "共享报表"},
        "updated_at": "2026-10-06T08:00:00Z",
    },
    {
        "model": "card",
        "id": 102,
        "name": "卡B",
        "collection": {"id": "root", "name": "Our analytics"},
        "updated_at": "2026-10-05T08:00:00Z",
    },
    {
        "model": "dashboard",
        "id": 201,
        "name": "板X",
        "collection": {"id": "root", "name": "Our analytics"},
        "updated_at": "2026-10-03T08:00:00Z",
    },
    {
        "model": "card",
        "id": 101,
        "name": "卡A",
        "collection": {"id": "root", "name": "Our analytics"},
        "updated_at": "2026-10-01T08:00:00Z",
    },
    {
        "model": "dashboard",
        "id": 202,
        "name": "板Y",
        "collection": {"id": 663, "name": "共享报表"},
        "updated_at": "2026-09-28T08:00:00Z",
    },
]

EXPECTED_GOLDEN = {
    "totals": {"databases": 3, "collections": 2, "cards": 5, "dashboards": 3},
    "databases": [
        {"id": 7, "name": "bi_data", "engine": "mysql", "table_count": 2},
        {"id": 15, "name": "quant_data", "engine": "mysql", "table_count": 1},
        {"id": 2, "name": "sample", "engine": "h2", "table_count": 0},
    ],
    "collections": [
        {"collection": {"id": "root", "name": "Our analytics"}, "cards": 3, "dashboards": 1},
        {"collection": {"id": 663, "name": "共享报表"}, "cards": 2, "dashboards": 2},
    ],
    "recent": EXPECTED_RECENT,
}


# -- fast path (include=tables) golden ---------------------------------------------


def test_overview_json_golden_fast_path(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    payload = result.json()
    assert payload == EXPECTED_GOLDEN  # exact §13.2 shape, no "slow" key
    assert "slow" not in payload
    # table_count keeps the `mbcli tables` default visibility rule: the hidden
    # and inactive tables of quant_data (15) are NOT counted
    assert payload["databases"][1]["table_count"] == 1


def test_overview_top_limits_recent_after_global_sort(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--top", "3").assert_ok(0)
    assert result.json()["recent"] == EXPECTED_RECENT[:3]


def test_overview_default_top_is_10(cli, mock_api):
    # 11 root cards (09-01..09-11) + 2 共享报表 items (10-06, 09-28) = 13 valid
    # recents -> default --top 10 keeps the global 10 newest
    big_root = [
        {
            "id": 1000 + i,
            "model": "card",
            "name": f"批量卡{i}",
            "updated_at": f"2026-09-{i + 1:02d}T00:00:00Z",
        }
        for i in range(11)
    ]
    _mock_common(mock_api, items={"root": big_root, "663": ITEMS["663"]})
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    recent = result.json()["recent"]
    assert len(recent) == 10
    assert [item["id"] for item in recent[:3]] == [104, 202, 1010]  # 10-06 > 09-28 > 09-11


def test_overview_root_synthesized_when_listed_absent(cli, mock_api):
    routes = _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    collections = result.json()["collections"]
    assert collections[0]["collection"] == {"id": "root", "name": "Our analytics"}
    assert routes["items:root"].call_count == 3  # card probe + dashboard probe + page


def test_overview_root_not_duplicated_when_server_lists_it(cli, mock_api):
    listed = [
        {"id": "root", "name": "Our analytics", "parent_id": None, "is_personal": False}
    ] + COLLECTIONS_LIST
    _mock_common(mock_api, collections=listed)
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    ids = [entry["collection"]["id"] for entry in result.json()["collections"]]
    assert ids == ["root", 663]


def test_overview_personal_and_nested_collections_filtered(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    ids = [entry["collection"]["id"] for entry in result.json()["collections"]]
    assert ids == ["root", 663]  # 700 (personal top-level), 900/701 (nested) dropped


def test_overview_exactly_two_total_probes_per_collection(cli, mock_api):
    routes = _mock_common(mock_api)
    cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    for coll in ("root", "663"):
        calls = routes[f"items:{coll}"].calls
        card_probes = [
            c
            for c in calls
            if c.request.url.params.get("limit") == "1"
            and c.request.url.params.get_list("models") == ["card"]
        ]
        dashboard_probes = [
            c
            for c in calls
            if c.request.url.params.get("limit") == "1"
            and c.request.url.params.get_list("models") == ["dashboard"]
        ]
        pages = [
            c
            for c in calls
            if c.request.url.params.get("limit") == "100"
            and c.request.url.params.get_list("models") == ["card", "dashboard"]
        ]
        assert len(card_probes) == 1
        assert len(dashboard_probes) == 1
        assert len(pages) == 1
        assert len(calls) == 3  # 2 quick total probes + 1 recents page


def test_overview_collection_cap_50(cli, mock_api):
    colls = [
        {"id": i, "name": f"c{i}", "parent_id": None, "is_personal": False} for i in range(1, 61)
    ]  # 60 non-personal top-level collections + root
    totals = {**TOTALS}
    for i in range(1, 61):
        totals[(str(i), "card")] = 0
        totals[(str(i), "dashboard")] = 0
    routes = _mock_common(mock_api, collections=colls, totals=totals)
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    payload = result.json()
    ids = [entry["collection"]["id"] for entry in payload["collections"]]
    assert len(ids) == 50
    assert ids[0] == "root"
    assert ids[-1] == 49  # root + the first 49 listed collections
    # every kept collection cost exactly its 2 total probes (all empty: no pages)
    probe_calls = [
        call
        for key, route in routes.items()
        if key.startswith("items:")
        for call in route.calls
        if call.request.url.params.get("limit") == "1"
    ]
    assert len(probe_calls) == 100


def test_overview_empty_collection_skips_recents_page(cli, mock_api):
    colls = COLLECTIONS_LIST + [
        {"id": 800, "name": "空集合", "parent_id": None, "is_personal": False}
    ]
    totals = {**TOTALS, ("800", "card"): 0, ("800", "dashboard"): 0}
    routes = _mock_common(mock_api, collections=colls, totals=totals, items={**ITEMS, "800": []})
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    payload = result.json()
    assert {"collection": {"id": 800, "name": "空集合"}, "cards": 0, "dashboards": 0} in (
        payload["collections"]
    )
    assert routes["items:800"].call_count == 2  # only the total probes, no page call


# -- fallback (slow) path ------------------------------------------------------------

PLAIN_DATABASES = {
    "data": [
        {"id": 7, "name": "bi_data", "engine": "mysql"},
        {"id": 2, "name": "sample", "engine": "h2"},
    ],
    "total": 2,
}

SHARED_COLLECTION = [{"id": 663, "name": "共享报表", "parent_id": None, "is_personal": False}]


def test_overview_fallback_when_include_param_rejected(cli, mock_api):
    seen: list[httpx.Request] = []

    def database_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "include" in request.url.params:
            return httpx.Response(400, json={"errors": {"include": "unknown parameter"}})
        return httpx.Response(200, json=PLAIN_DATABASES)

    mock_api.get(f"{BASE}/api/database").mock(side_effect=database_handler)
    mock_api.get(f"{BASE}/api/collection").mock(
        return_value=httpx.Response(200, json=SHARED_COLLECTION)
    )
    _mock_items(mock_api)
    meta7 = mock_api.get(f"{BASE}/api/database/7/metadata").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 7,
                "tables": [
                    _table(1, "t1"),
                    _table(2, "t2"),
                    _table(9, "hid", visibility_type="hidden"),
                ],
            },
        )
    )
    meta2 = mock_api.get(f"{BASE}/api/database/2/metadata").mock(
        return_value=httpx.Response(200, json={"id": 2, "tables": []})
    )
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    payload = result.json()
    assert payload["slow"] is True
    assert list(payload) == ["totals", "databases", "collections", "recent", "slow"]
    assert payload["databases"] == [
        {"id": 7, "name": "bi_data", "engine": "mysql", "table_count": 2},
        {"id": 2, "name": "sample", "engine": "h2", "table_count": 0},
    ]
    assert meta7.call_count == 1
    assert meta2.call_count == 1
    # include=tables was attempted once, then the plain retry
    assert ["include" in call.url.params for call in seen] == [True, False]


def test_overview_fallback_when_tables_arrays_missing(cli, mock_api):
    # an old instance silently ignoring `include=tables`: 200 but no tables[]
    _mock_common(mock_api, databases_payload=PLAIN_DATABASES)
    meta7 = mock_api.get(f"{BASE}/api/database/7/metadata").mock(
        return_value=httpx.Response(200, json={"id": 7, "tables": [_table(1, "t1")]})
    )
    meta2 = mock_api.get(f"{BASE}/api/database/2/metadata").mock(
        return_value=httpx.Response(200, json={"id": 2, "tables": []})
    )
    result = cli("--url", BASE, "--api-key", "k", "overview").assert_ok(0)
    payload = result.json()
    assert payload["slow"] is True
    assert [d["table_count"] for d in payload["databases"]] == [1, 0]
    assert meta7.call_count == 1 and meta2.call_count == 1


def test_overview_no_fallback_on_permission_error(cli, mock_api):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(403, text="You don't have permissions to do that.")

    mock_api.get(f"{BASE}/api/database").mock(side_effect=handler)
    meta7 = mock_api.get(f"{BASE}/api/database/7/metadata").mock(
        return_value=httpx.Response(200, json={"id": 7, "tables": []})
    )
    result = cli("--url", BASE, "--api-key", "k", "overview")
    assert result.exit_code == 1
    assert result.error()["code"] == "permission_denied"
    assert len(seen) == 1  # no plain /api/database retry
    assert meta7.call_count == 0  # and no per-db metadata loop


def test_overview_slow_note_in_markdown_render(cli, mock_api):
    _mock_common(mock_api, databases_payload=PLAIN_DATABASES)
    mock_api.get(f"{BASE}/api/database/7/metadata").mock(
        return_value=httpx.Response(200, json={"id": 7, "tables": [_table(1, "t1")]})
    )
    mock_api.get(f"{BASE}/api/database/2/metadata").mock(
        return_value=httpx.Response(200, json={"id": 2, "tables": []})
    )
    result = cli("--url", BASE, "--api-key", "k", "overview", "--format", "markdown").assert_ok(0)
    assert "(slow path" in result.stdout


# -- render formats ------------------------------------------------------------------


def test_overview_markdown_sections(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--format", "markdown").assert_ok(0)
    out = result.stdout
    assert out.startswith("Totals: 3 databases, 2 collections, 5 cards, 3 dashboards")
    assert "| id | name | engine | table_count |" in out
    assert "| id | name | cards | dashboards |" in out
    assert "| model | id | name | collection | updated_at |" in out
    assert "| card | 103 | 卡C | Our analytics | 2026-10-07T09:00:00Z |" in out
    assert "| card | 104 | 卡D | 共享报表 | 2026-10-06T08:00:00Z |" in out


def test_overview_table_sections(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--format", "table").assert_ok(0)
    out = result.stdout
    assert "Totals: 3 databases, 2 collections, 5 cards, 3 dashboards" in out
    for section in ("Databases", "Collections", "Recent"):
        assert section in out
    for cell in ("table_count", "共享报表", "卡C", "2026-10-07T09:00:00Z"):
        assert cell in out


def test_overview_csv_is_invalid_args(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--format", "csv")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_overview_ndjson_is_invalid_args(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--format", "ndjson")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_overview_top_must_be_positive(cli, mock_api):
    _mock_common(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "overview", "--top", "0")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_overview_out_file_summary(cli, mock_api, tmp_path):
    _mock_common(mock_api)
    target = tmp_path / "overview.json"
    result = cli("--url", BASE, "--api-key", "k", "overview", "--out", str(target)).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 1, "truncated": False}
    assert json.loads(target.read_text("utf-8")) == EXPECTED_GOLDEN
