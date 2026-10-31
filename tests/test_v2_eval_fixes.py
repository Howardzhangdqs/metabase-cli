"""v0.2.0 evaluation-fix regression tests (P0 + P1-1, eval 2026-10-31).

Pins the --param contract live-verified against the reference Metabase
instance (47.100.241.247:3000, read-only probes):

- a native variable referenced as {{k}} is only matched when the tag is
  DECLARED in native.template-tags (with an id) and the parameter object
  carries a stable id — the pre-fix body (empty template-tags, id-less
  params) got 400 ``Cannot run the query: missing required parameters:
  #{"n"}`` (and a date tag without id got the same 400);
- parameter "type": "category" is accepted for text/number/date tags alike
  ("text"/"number" also accepted; "date" alone is NOT);
- cards run maps --param onto the card's DECLARED parameter ids (dashboard
  parameters by name/slug; native template-tags by tag name).

The reference instance has NO parameterized card (≈985 cards inspected via
catalog/search/dashboard mappings — all with empty parameters and empty
template-tags), so the cards-run id mapping is pinned here offline against
canned card payloads shaped like real /api/card responses.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

BASE = "http://test.local"
REPO = Path(__file__).resolve().parent.parent


def dataset_payload(rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "completed",
        "data": {
            "cols": [{"name": "n", "base_type": "type/BigInteger", "database_type": "BIGINT"}],
            "rows": rows,
            "rows_truncated": False,
            "native_form": {"query": "SELECT …", "template-tags": {}},
        },
        "row_count": len(rows),
        "running_time": 5,
        "cached": False,
    }


def mock_dataset(mock_api: Any, payload: dict[str, Any]) -> Any:
    return mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(200, json=payload))


def sent_body(route: Any) -> dict[str, Any]:
    return json.loads(route.calls.last.request.content)


# == P1-1: query --param body shape (id + template-tags declarations) =============


def run_query(cli: Any, mock_api: Any, *extra: object) -> dict[str, Any]:
    route = mock_dataset(mock_api, dataset_payload([[1]]))
    cli(
        "--url", BASE, "--api-key", "k", "query", "--db", "15", "--native", "SELECT {{n}}", *extra
    ).assert_ok(0)
    return sent_body(route)


def test_param_body_has_stable_ids_and_declares_template_tags(cli, mock_api):
    body = run_query(cli, mock_api, "--param", "n=1")
    param, tag = body["parameters"][0], body["native"]["template-tags"]["n"]
    assert set(param) == {"id", "type", "target", "value"}
    assert set(tag) == {"id", "name", "display-name", "type", "required"}
    # ids are distinct well-formed uuids (parameter id ≠ tag id; both accepted live)
    assert param["id"] != tag["id"]
    uuid.UUID(param["id"])
    uuid.UUID(tag["id"])
    assert param["type"] == "category"
    assert param["target"] == ["variable", ["template-tag", "n"]]
    assert param["value"] == "1"
    assert tag == {
        "id": tag["id"],
        "name": "n",
        "display-name": "n",
        "type": "number",
        "required": False,
    }


def test_param_no_params_keeps_template_tags_empty(cli, mock_api):
    body = run_query(cli, mock_api)
    assert body["parameters"] == []
    assert body["native"]["template-tags"] == {}


@pytest.mark.parametrize(
    ("value", "tag_type"),
    [
        ("1", "number"),
        ("-3", "number"),
        ("2.5", "number"),
        ("1e3", "number"),
        ("2023-07-01", "date"),
        ("2023-07-01T08:00", "date"),
        ("2023-07-01 08:00:30+08:00", "date"),
        ("2023-07-01T08:00:30.123Z", "date"),
        ("上海", "text"),
        ("a=b", "text"),
        ("2023-13-99", "date"),  # inference is shape-based; no calendar validation
        ("", "text"),
        ("2024-13", "text"),  # partial dates are not ISO dates
    ],
)
def test_param_tag_type_inference_matrix(value: str, tag_type: str) -> None:
    from mbcli.commands.query import infer_tag_type

    assert infer_tag_type(value) == tag_type


def test_param_repeated_keys_keep_last_value_and_one_tag(cli, mock_api):
    body = run_query(cli, mock_api, "--param", "n=1", "--param", "n=2")
    assert [p["value"] for p in body["parameters"]] == ["1", "2"]
    assert set(body["native"]["template-tags"]) == {"n"}


# == P1-1c: cards run --param maps onto declared ids =============================


def card_payload(
    parameters: list[dict[str, Any]] | None = None,
    template_tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": 9001,
        "name": "带参卡片",
        "parameters": parameters or [],
        "dataset_query": {
            "database": 15,
            "type": "native",
            "native": {
                "query": "select {{n}}",
                "template-tags": template_tags or {},
            },
        },
    }


def run_card(cli: Any, mock_api: Any, card: dict[str, Any], *extra: object) -> dict[str, Any]:
    mock_api.get(f"{BASE}/api/card/9001").mock(return_value=httpx.Response(200, json=card))
    route = mock_api.post(f"{BASE}/api/card/9001/query").mock(
        return_value=httpx.Response(200, json=dataset_payload([[1]]))
    )
    cli("--url", BASE, "--api-key", "k", "cards", "run", "9001", *extra).assert_ok(0)
    return sent_body(route)


def test_cards_run_param_reuses_card_parameter_id_by_slug_and_name(cli, mock_api):
    card = card_payload(
        parameters=[
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "name": "城市",
                "slug": "city",
                "type": "string/=",
                "target": ["dimension", ["template-tag", "city"]],
            }
        ]
    )
    # match by slug …
    body = run_card(cli, mock_api, card, "--param", "city=上海", "--param", "城市=上海")
    assert body["ignore_cache"] is False
    assert body["parameters"] == [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "type": "string/=",
            "target": ["dimension", ["template-tag", "city"]],
            "value": "上海",
        },
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "type": "string/=",
            "target": ["dimension", ["template-tag", "city"]],
            "value": "上海",
        },
    ]


def test_cards_run_param_reuses_template_tag_id_for_native_variables(cli, mock_api):
    card = card_payload(
        template_tags={
            "n": {
                "id": "22222222-2222-4222-8222-222222222222",
                "name": "n",
                "display-name": "n",
                "type": "number",
                "required": True,
            }
        }
    )
    body = run_card(cli, mock_api, card, "--param", "n=7")
    assert body["parameters"] == [
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "type": "number",
            "target": ["variable", ["template-tag", "n"]],
            "value": "7",
        }
    ]


def test_cards_run_unmatched_param_falls_back_to_fresh_id(cli, mock_api):
    body = run_card(cli, mock_api, card_payload(), "--param", "other=1")
    param = body["parameters"][0]
    assert set(param) == {"id", "type", "target", "value"}
    assert uuid.UUID(param["id"])
    assert param["type"] == "category"
    assert param["target"] == ["variable", ["template-tag", "other"]]
    assert param["value"] == "1"


def test_cards_run_without_param_skips_card_fetch(cli, mock_api):
    route = mock_api.post(f"{BASE}/api/card/9001/query").mock(
        return_value=httpx.Response(200, json=dataset_payload([[1]]))
    )
    cli("--url", BASE, "--api-key", "k", "cards", "run", "9001").assert_ok(0)
    assert sent_body(route) == {"parameters": [], "ignore_cache": False}


def test_cards_run_malformed_param_exits_2_before_network(cli, mock_api):
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "9001", "--param", "noequals")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"
    assert not mock_api.calls  # validation precedes any request (incl. card fetch)


# == P0 治本: build-single MODULES completeness (清单完整性自检契约) ===============


def _load_build_single() -> Any:
    path = REPO / "scripts" / "build-single.py"
    spec = importlib.util.spec_from_file_location("build_single_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_single_modules_cover_every_source_module() -> None:
    """MODULES must list every src/mbcli module exactly once (P0 root cause)."""
    build_single = _load_build_single()
    actual: set[str] = set()
    for path in (REPO / "src" / "mbcli").rglob("*.py"):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        actual.add(
            path.relative_to(REPO / "src" / "mbcli").with_suffix("").as_posix().replace("/", ".")
        )
    assert actual == set(build_single.MODULES), (
        f"scripts/build-single.py MODULES drifted: missing={sorted(actual - set(build_single.MODULES))} "
        f"stale={sorted(set(build_single.MODULES) - actual)}"
    )
    assert len(build_single.MODULES) == len(set(build_single.MODULES)), "duplicate MODULES entry"
    build_single.check_modules_complete()  # the generator's own gate must pass too
    # the P0 regression specifically: reserved must precede client (client.py
    # imports find_reserved_token from it)
    assert build_single.MODULES.index("reserved") < build_single.MODULES.index("client")
