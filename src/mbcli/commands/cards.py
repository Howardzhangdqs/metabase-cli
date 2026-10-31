"""`mbcli cards run|show` (DESIGN §4; M2/M3)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import uuid4

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


def card_param_specs(params: list[str] | None, card: dict[str, Any]) -> list[dict[str, Any]]:
    """Map `--param k=v` onto a card's DECLARED parameter ids (v0.2).

    The card declares its parameters in one of two places (read via
    `GET /api/card/{id}`, the same payload `cards show` renders):

    - ``card["parameters"]``: dashboard-style defs, each with
      id/name/slug/type (and possibly target) — `--param` matches by
      ``slug`` or ``name`` and REUSES the declared id/type/target;
    - ``dataset_query.native["template-tags"]``: native variable defs with
      id/name/type — matched by tag name, id reused, target stays
      ``["variable", ["template-tag", k]]``.

    Unmatched params fall back to a fresh uuid + "category" variable target
    (the M2 shape plus a stable id). Live note: `POST /api/card/{id}/query`
    accepts this body shape (parameter ids must exist when the card declares
    them — the query-side equivalent was probe-verified against a real
    server; the reference instance has no parameterized card for an
    end-to-end run, see tests/test_v2_eval_fixes.py).
    """
    declared: dict[str, dict[str, Any]] = {}
    for spec in card.get("parameters") or []:
        if isinstance(spec, dict) and spec.get("id"):
            for alias in (spec.get("slug"), spec.get("name")):
                if isinstance(alias, str) and alias:
                    declared.setdefault(alias, spec)
    tags = ((card.get("dataset_query") or {}).get("native") or {}).get("template-tags") or {}
    if isinstance(tags, dict):
        for name, tag in tags.items():
            if isinstance(name, str) and isinstance(tag, dict) and tag.get("id"):
                declared.setdefault(name, tag)
    specs: list[dict[str, Any]] = []
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
        else:
            specs.append(
                {
                    "id": str(uuid4()),
                    "type": "category",
                    "target": ["variable", ["template-tag", key]],
                    "value": value,
                }
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
