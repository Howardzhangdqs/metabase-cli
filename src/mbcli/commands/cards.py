"""`mbcli cards run|show` (DESIGN §4; M2/M3)."""

from __future__ import annotations

import re
from typing import Annotated, Any

import typer

from mbcli.commands.query import envelope_from_payload, parse_param_items
from mbcli.errors import MbcliError
from mbcli.models import CardDetail
from mbcli.options import ParamOpt, state_from_ctx
from mbcli.output import (
    FieldsOpt,
    Format,
    FormatOpt,
    MaxRowsOpt,
    OutOpt,
    emit_envelope,
    emit_object,
    parse_fields,
    prune_fields,
)

app = typer.Typer(help="执行或查看已保存的问题（卡片）。", no_args_is_help=True)

# Metabase native template variable reference: {{name}} (whitespace-tolerant;
# inside [[optional blocks]] the inner {{var}} still matches).
_TEMPLATE_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _card_dataset_query(card: dict[str, Any]) -> dict[str, Any]:
    dq = card.get("dataset_query")
    return dq if isinstance(dq, dict) else {}


def _card_native_sql(card: dict[str, Any]) -> str:
    """Best-effort native SQL text of a card (classic or pMBQL shape)."""
    dq = _card_dataset_query(card)
    native = dq.get("native")
    chunks: list[str] = []
    if isinstance(native, dict) and isinstance(native.get("query"), str):
        chunks.append(native["query"])
    stages = dq.get("stages")
    if isinstance(stages, list):  # pMBQL: {"lib/type": "mbql/query", "stages": […]}
        for stage in stages:
            if isinstance(stage, dict) and isinstance(stage.get("native"), str):
                chunks.append(stage["native"])
    return "\n".join(chunks)


def _card_declared_params(card: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Collect every parameter the card DECLARES, keyed by bindable name.

    Three declaration surfaces (Round-1 eval P0-1 probe of card 2164, verbatim
    shapes in tests/test_v2_eval_fixes.py):

    1. ``card["parameters"]`` — dashboard-style defs with id/name/slug/type
       (and possibly target); matched by ``slug`` or ``name``.
    2. ``dataset_query.native["template-tags"]`` — the classic native dict
       ``{name: {id, name, type, …}}``.
    3. ``dataset_query.stages[*]["template-tags"]`` — pMBQL cards store a LIST
       of tag dicts (``{"id", "name", "display-name", "type", "required",
       "default"}``) under each mbql.stage/native. Card 2164 declares its
       ``category_name`` variable ONLY here — the v0.2.1 mapper missed it and
       silently ran with the tag's ``default`` instead.
    """
    declared: dict[str, dict[str, Any]] = {}

    def _offer(name: Any, spec: dict[str, Any]) -> None:
        if isinstance(name, str) and name and spec.get("id"):
            declared.setdefault(name, spec)

    for spec in card.get("parameters") or []:
        if isinstance(spec, dict):
            _offer(spec.get("slug"), spec)
            _offer(spec.get("name"), spec)

    dq = _card_dataset_query(card)
    native = dq.get("native")
    tags = native.get("template-tags") if isinstance(native, dict) else None
    if isinstance(tags, dict):
        for name, tag in tags.items():
            if isinstance(tag, dict):
                _offer(name, tag)
    stages = dq.get("stages")
    if isinstance(stages, list):
        for stage in stages:
            tags = stage.get("template-tags") if isinstance(stage, dict) else None
            if isinstance(tags, dict):
                for name, tag in tags.items():
                    if isinstance(tag, dict):
                        _offer(name, tag)
            elif isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, dict):
                        _offer(tag.get("name"), tag)
    return declared


def card_param_specs(params: list[str] | None, card: dict[str, Any]) -> list[dict[str, Any]]:
    """Map `--param k=v` onto a card's DECLARED parameters (Round-1 eval P0-1).

    Binding rules (live-verified against card 2164, a pMBQL card):

    a. the key matches a declared parameter (dashboard parameter by
       name/slug, template tag by tag name — classic dict or pMBQL stage
       list): send ``{"id": <declared id>, "type": <declared type|category>,
       "target": <declared target|variable target>, "value": v}``. The server
       REQUIRES the id — a parameter without id is 400
       ``{"errors": {"parameters": {"id": "Schema for a string that cannot be
       blank."}}}``, and an id that matches no declaration is silently
       ignored (the card runs with the tag's default — the P0-1 bug).
    b. the key is NOT declared but ``{{key}}`` appears in the card's native
       SQL (dormant template variable): hard fail invalid_args — NEVER run
       unfiltered.
    c. the key matches nothing at all: invalid_args listing the card's
       declared parameter names.
    """
    declared = _card_declared_params(card)
    sql_vars = set(_TEMPLATE_VAR.findall(_card_native_sql(card)))
    specs: list[dict[str, Any]] = []
    dormant: list[str] = []
    unknown: list[str] = []
    for key, value in parse_param_items(params):
        match = declared.get(key)
        if match is not None:
            specs.append(
                {
                    "id": str(match["id"]),
                    "type": match.get("type") or "category",
                    "target": match.get("target") or ["variable", ["template-tag", key]],
                    "value": value,
                }
            )
        elif key in sql_vars:
            dormant.append(key)
        else:
            unknown.append(key)
    if dormant:
        raise MbcliError(
            "invalid_args",
            "卡片未声明参数控件 "
            + "、".join(repr(k) for k in dormant)
            + "（SQL 含休眠模板变量），请用 `query --native` 复用其 SQL"
            "（`cards show <id>` 查看）",
            detail={"dormant": dormant, "declared": sorted(declared)},
        )
    if unknown:
        available = "、".join(sorted(declared)) if declared else "（该卡片未声明任何参数控件）"
        raise MbcliError(
            "invalid_args",
            "卡片未声明参数 " + "、".join(repr(k) for k in unknown) + "；可用参数: " + available,
            detail={"unknown": unknown, "declared": sorted(declared)},
        )
    return specs


@app.command(
    "run",
    epilog=(
        "示例:\n"
        "  mbcli cards run 7876\n"
        "  mbcli cards run 7876 --format csv --max-rows 100 --out result.csv"
    ),
)
def run(
    ctx: typer.Context,
    card_id: Annotated[int, typer.Argument(help="卡片 id。")],
    param: ParamOpt = None,
    format: FormatOpt = Format.json,
    max_rows: MaxRowsOpt = 1000,
    out: OutOpt = None,
    fields: FieldsOpt = None,
) -> None:
    """执行已保存的卡片（输出与 query 相同的信封）。"""
    parse_param_items(param)  # validate k=v shape before ANY network work (exit 2)
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        parameters: list[dict[str, Any]] = []
        if param:
            card_payload = client.get(f"/api/card/{card_id}")  # map --param → declared ids
            if not isinstance(card_payload, dict):
                raise MbcliError(
                    "invalid_response", f"/api/card/{card_id} response is not an object"
                )
            parameters = card_param_specs(param, card_payload)
        payload = client.post_json(
            f"/api/card/{card_id}/query",
            {"parameters": parameters, "ignore_cache": False},
        )
    emit_envelope(
        envelope_from_payload(payload, max_rows),
        fmt=format,
        fields=fields,
        out=out,
        no_color=state.no_color,
    )


@app.command(
    "show",
    epilog=("示例:\n  mbcli cards show 7876\n  mbcli cards show 7876 --fields id,name,sql"),
)
def show(
    ctx: typer.Context,
    card_id: Annotated[int, typer.Argument(help="卡片 id。")],
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """查看卡片定义（SQL/MBQL/参数/集合），不执行。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        payload = client.get(f"/api/card/{card_id}")
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "/api/card response is not an object")
    card = CardDetail.from_api(payload).to_dict()
    field_list = parse_fields(fields)
    if field_list:
        card = prune_fields([card], field_list)[0]
    emit_object(card, fmt=format, out=out, no_color=state.no_color)
