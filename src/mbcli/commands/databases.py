"""`mbcli databases list`: GET /api/database -> data[] (DESIGN §4, §5.1)."""

from __future__ import annotations

import typer

from mbcli.errors import MbcliError
from mbcli.models import DatabaseInfo
from mbcli.options import state_from_ctx
from mbcli.output import (
    FieldsOpt,
    Format,
    FormatOpt,
    OutOpt,
    emit_records,
)

app = typer.Typer(help="浏览数据库。", no_args_is_help=True)


@app.command(
    "list",
    epilog=("示例:\n  mbcli databases list\n  mbcli databases list --fields id,name --format csv"),
)
def list_databases(
    ctx: typer.Context,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出全部数据库（该端点无分页，一次全量）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        payload = client.get("/api/database")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/database response has no data array")
    records = [DatabaseInfo.from_api(item).to_dict() for item in data if isinstance(item, dict)]
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
