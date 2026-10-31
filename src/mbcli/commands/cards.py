"""`mbcli cards run|show` (DESIGN §4; M2/M3)."""

from __future__ import annotations

from typing import Annotated

import typer

from mbcli.commands.query import envelope_from_payload, native_parameters
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
    parameters = native_parameters(param)
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
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
