"""Resolver (DESIGN §3.5): id passthrough, name lookup + caching, ambiguity, root."""

from __future__ import annotations

import httpx
import pytest

from mbcli.client import MetabaseClient
from mbcli.config import Config
from mbcli.errors import MbcliError
from mbcli.resolver import Resolver

BASE = "http://test.local"

DATABASES = {
    "data": [
        {"id": 1, "name": "dup"},
        {"id": 2, "name": "dup"},
        {"id": 15, "name": "quant_data"},
    ],
    "total": 3,
}

METADATA_15 = {
    "tables": [
        {"id": 100, "name": "app_mau"},
        {"id": 200, "name": "dup_table"},
        {"id": 201, "name": "dup_table"},
    ]
}

COLLECTIONS = [
    {"id": 663, "name": "共享报表"},
    {"id": 700, "name": "dup_coll"},
    {"id": 701, "name": "dup_coll"},
]


@pytest.fixture
def client() -> MetabaseClient:
    return MetabaseClient(
        Config(url=BASE, api_key="k", session_cache="/tmp/none.json"), retry_backoffs=()
    )


@pytest.fixture
def resolver(client: MetabaseClient, mock_api) -> Resolver:
    mock_api.get(f"{BASE}/api/database").mock(return_value=httpx.Response(200, json=DATABASES))
    mock_api.get(f"{BASE}/api/database/15/metadata").mock(
        return_value=httpx.Response(200, json=METADATA_15)
    )
    mock_api.get(f"{BASE}/api/collection").mock(return_value=httpx.Response(200, json=COLLECTIONS))
    return Resolver(client)


# -- databases ---------------------------------------------------------------


def test_db_id_passthrough_no_http(resolver, mock_api):
    assert resolver.db("15") == 15
    assert mock_api.get(f"{BASE}/api/database").call_count == 0


def test_db_name_resolved(resolver):
    assert resolver.db("quant_data") == 15


def test_db_name_lookup_cached(resolver, mock_api):
    assert resolver.db("quant_data") == 15
    assert resolver.db("quant_data") == 15
    assert mock_api.get(f"{BASE}/api/database").call_count == 1


def test_db_unknown_name_not_found(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.db("no_such_db")
    assert excinfo.value.code == "not_found"


def test_db_ambiguous_lists_candidates(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.db("dup")
    assert excinfo.value.code == "ambiguous_ref"
    candidates = excinfo.value.detail["candidates"]
    assert sorted(c["id"] for c in candidates) == [1, 2]


def test_db_id_first_character_numeric_passthrough(resolver, mock_api):
    # "007" is still an id, never a name lookup
    assert resolver.db("007") == 7


# -- tables ---------------------------------------------------------------------


def test_table_id_passthrough_without_db(resolver, mock_api):
    assert resolver.table("3258") == 3258
    assert mock_api.get(f"{BASE}/api/database/15/metadata").call_count == 0


def test_table_name_with_db_id(resolver):
    assert resolver.table("app_mau", db_id=15) == 100


def test_table_name_without_db_is_invalid_args(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.table("app_mau")
    assert excinfo.value.code == "invalid_args"
    assert excinfo.value.exit_code == 2
    assert "--db" in excinfo.value.message


def test_table_name_ambiguous(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.table("dup_table", db_id=15)
    assert excinfo.value.code == "ambiguous_ref"
    assert sorted(c["id"] for c in excinfo.value.detail["candidates"]) == [200, 201]


def test_table_name_unknown(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.table("missing", db_id=15)
    assert excinfo.value.code == "not_found"


def test_table_metadata_cached(resolver, mock_api):
    resolver.table("app_mau", db_id=15)
    resolver.table("app_mau", db_id="15")
    assert mock_api.get(f"{BASE}/api/database/15/metadata").call_count == 1


# -- collections --------------------------------------------------------------------


def test_collection_root_maps_to_string_root(resolver, mock_api):
    assert resolver.collection("root") == "root"
    assert mock_api.get(f"{BASE}/api/collection").call_count == 0


def test_collection_id_passthrough(resolver, mock_api):
    assert resolver.collection("663") == 663
    assert mock_api.get(f"{BASE}/api/collection").call_count == 0


def test_collection_name_resolved(resolver):
    assert resolver.collection("共享报表") == 663


def test_collection_name_ambiguous(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.collection("dup_coll")
    assert excinfo.value.code == "ambiguous_ref"
    assert sorted(c["id"] for c in excinfo.value.detail["candidates"]) == [700, 701]


def test_collection_unknown_name_not_found(resolver):
    with pytest.raises(MbcliError) as excinfo:
        resolver.collection("不存在")
    assert excinfo.value.code == "not_found"
