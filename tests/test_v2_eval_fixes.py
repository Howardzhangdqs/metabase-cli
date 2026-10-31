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


# Card 2164 ("线上消费-京东-二级类目-销售分析") — verbatim probe shape
# (Round-1 eval P0-1): a pMBQL card. parameters is EMPTY; the variable is
# declared ONLY as a LIST under dataset_query.stages[*].template-tags, with a
# default ("服饰") the server falls back to when a parameter fails to bind.
PMBQL_CARD_2164 = {
    "id": 2164,
    "name": "线上消费-京东-二级类目-销售分析",
    "parameters": [],
    "dataset_query": {
        "lib/type": "mbql/query",
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "template-tags": [
                    {
                        "required": True,
                        "id": "bbdc56f9-9ce5-d42c-a901-c5ebbe7e13ee",
                        "name": "category_name",
                        "display-name": "京东-一级类目",
                        "type": "text",
                        "default": "服饰",
                    }
                ],
                "native": (
                    "select date,sum(sales_value) as sales_value\n"
                    "from tab_ec_jd_cat_yoy\n"
                    "where sector_cn = {{category_name}} and date > '2019-12'\n"
                    "group by date\norder by date asc;"
                ),
            }
        ],
        "database": 10,
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


def test_cards_run_param_reuses_pmbql_stage_tag_id(cli, mock_api):
    """P0-1 live shape: pMBQL cards declare variables as a LIST under
    dataset_query.stages[*].template-tags (probe of card 2164). The v0.2.1
    mapper only read the classic native.template-tags dict, missed the
    declaration and silently ran with the tag's default."""
    route = mock_api.get(f"{BASE}/api/card/2164").mock(
        return_value=httpx.Response(200, json=PMBQL_CARD_2164)
    )
    query_route = mock_api.post(f"{BASE}/api/card/2164/query").mock(
        return_value=httpx.Response(200, json=dataset_payload([[1]]))
    )
    cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "cards",
        "run",
        "2164",
        "--param",
        "category_name=家用电器",
    ).assert_ok(0)
    assert route.called
    assert sent_body(query_route)["parameters"] == [
        {
            "id": "bbdc56f9-9ce5-d42c-a901-c5ebbe7e13ee",
            "type": "text",
            "target": ["variable", ["template-tag", "category_name"]],
            "value": "家用电器",
        }
    ]


def test_cards_run_dormant_template_var_hard_fails(cli, mock_api):
    """P0-1 rule b: {{var}} present in SQL but declared NOWHERE — the server
    cannot bind it, so running would silently use no filter. Hard fail."""
    card = card_payload(template_tags={})  # SQL still references {{n}}
    mock_api.get(f"{BASE}/api/card/9001").mock(return_value=httpx.Response(200, json=card))
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "9001", "--param", "n=1")
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert "卡片未声明参数控件" in error["message"]
    assert "'n'" in error["message"]
    assert "query --native" in error["message"]
    assert "cards show" in error["message"]
    assert error["detail"]["dormant"] == ["n"]


def test_cards_run_unknown_param_lists_declared_names(cli, mock_api):
    """P0-1 rule c: a key matching nothing → invalid_args naming what IS
    available (never a silent unfiltered run)."""
    card = card_payload(
        template_tags={
            "n": {
                "id": "22222222-2222-4222-8222-222222222222",
                "name": "n",
                "display-name": "n",
                "type": "number",
                "required": False,
            }
        }
    )
    mock_api.get(f"{BASE}/api/card/9001").mock(return_value=httpx.Response(200, json=card))
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "9001", "--param", "other=1")
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert "'other'" in error["message"]
    assert "n" in error["message"]  # lists the card's actual parameter names
    assert error["detail"]["unknown"] == ["other"]
    assert error["detail"]["declared"] == ["n"]


def test_cards_run_unknown_param_on_undeclared_card_names_nothing(cli, mock_api):
    mock_api.get(f"{BASE}/api/card/9001").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 9001,
                "parameters": [],
                "dataset_query": {
                    "type": "native",
                    "native": {"query": "select 1", "template-tags": {}},
                },
            },
        )
    )
    result = cli("--url", BASE, "--api-key", "k", "cards", "run", "9001", "--param", "x=1")
    assert result.exit_code == 2
    assert "未声明任何参数控件" in result.error()["message"]


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


# == Round-1 P1-1: unrecognized leading keyword is a usage error, not write ====


def test_query_unknown_leading_keyword_is_invalid_args_not_write_blocked(cli, mock_api):
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "query",
        "--db",
        "10",
        "--native",
        "SELECTNoSuch FROM x LIMIT 1",
    )
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert error["detail"]["keyword"] == "SELECTNOSUCH"
    assert "--allow-write" not in error["message"]
    assert "无法识别的首关键词 `SELECTNOSUCH`" in error["message"]
    assert not mock_api.calls  # guard runs before any network work


# == Round-1 P2-1: search items carry database_name ==============================


def test_search_unknown_database_id_yields_null_name(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json={"data": [{"id": 13, "name": "bi_data"}], "total": 1})
    )
    mock_api.get(f"{BASE}/api/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": 1,
                        "model": "card",
                        "name": "ghost",
                        "collection": None,
                        "database_id": 999,  # not in the db index
                        "updated_at": "2026-10-01T00:00:00Z",
                    }
                ],
                "total": 1,
            },
        )
    )
    result = cli("--url", BASE, "--api-key", "k", "search", "ghost").assert_ok(0)
    assert result.json()[0]["database_name"] is None


# == Round-1 P2-3: MB_CONFIG_DIR config file resolution ==========================


def _write_toml(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_mb_config_dir_directs_config_file_default(tmp_path) -> None:
    from mbcli.config import load_config

    cfg_dir = tmp_path / "mbcli-config"
    _write_toml(
        cfg_dir / "mbcli.toml",
        '[metabase]\nurl = "http://from-dir-toml"\nusername = "u"\npassword = "p"\n',
    )
    config = load_config(env={"MB_CONFIG_DIR": str(cfg_dir), "MB_URL": "http://env-wins"})
    assert config.config_file == str(cfg_dir / "mbcli.toml")
    assert config.url == "http://env-wins"  # env still outranks the toml
    assert config.username == "u"  # the redirected file IS read


def test_mb_config_file_wins_over_mb_config_dir(tmp_path) -> None:
    from mbcli.config import load_config

    cfg_dir = tmp_path / "dir"
    _write_toml(cfg_dir / "mbcli.toml", '[metabase]\nurl = "http://dir"\n')
    explicit = tmp_path / "explicit.toml"
    _write_toml(explicit, '[metabase]\nurl = "http://explicit"\n')
    config = load_config(
        env={
            "MB_CONFIG_DIR": str(cfg_dir),
            "MB_CONFIG_FILE": str(explicit),
            "MB_URL": "http://x",
        }
    )
    assert config.config_file == str(explicit)
    assert config.username is None  # explicit file (no creds) was honored


def test_mb_config_dir_does_not_move_session_cache(tmp_path) -> None:
    from mbcli.config import default_session_cache, load_config

    config = load_config(
        env={"MB_CONFIG_DIR": str(tmp_path), "MB_URL": "http://x"},
        toml_path=tmp_path / "none.toml",
    )
    # session cache stays at ~/.cache unless MB_SESSION_CACHE says otherwise
    assert config.session_cache == default_session_cache()
