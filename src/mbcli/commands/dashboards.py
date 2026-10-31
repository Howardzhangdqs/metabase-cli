"""`mbcli dashboards show` (DESIGN §4; M3)."""

from __future__ import annotations

from typing import Annotated

import typer

from mbcli.errors import MbcliError
from mbcli.models import DashboardDetail
from mbcli.options import state_from_ctx
from mbcli.output import (
    FieldsOpt,
    Format,
    FormatOpt,
    OutOpt,
    emit_object,
    parse_fields,
    prune_fields,
)

app = typer.Typer(help="查看仪表盘。", no_args_is_help=True)


@app.command(
    "show",
    epilog=(
        "示例:\n  mbcli dashboards show 1607\n  mbcli dashboards show 1607 --fields id,name,cards"
    ),
)
def show(
    ctx: typer.Context,
    dashboard_id: Annotated[int, typer.Argument(help="仪表盘 id。")],
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """查看仪表盘结构（tabs/parameters/cards 含位置与参数映射），不执行任何卡片。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        payload = client.get(f"/api/dashboard/{dashboard_id}")
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "dashboard response is not an object")
    record = DashboardDetail.from_api(payload).to_dict()
    if fields:
        # treat the single detail object as a one-record list: unknown key -> invalid_args
        record = prune_fields([record], parse_fields(fields))[0]
    emit_object(record, fmt=format, out=out, no_color=state.no_color)
