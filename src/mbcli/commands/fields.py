"""`mbcli fields`: fields of a table (DESIGN §4, §5.1)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.config import NO_DB_MESSAGE
from mbcli.errors import MbcliError
from mbcli.models import FieldInfo
from mbcli.options import IncludeHiddenOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    "  mbcli fields --table app_mau --db quant_data\n"
    "  mbcli fields --table 3258        # 数字表 id 无需 --db"
)


def _to_field_info(payload: dict[str, Any]) -> FieldInfo:
    """§5.1 `position` reports the physical column ordinal, which query_metadata
    carries as `database_position` (its `position` is the UI ordering override),
    so normalize the key before the whitelist mapping in models.FieldInfo."""
    return FieldInfo.from_api({**payload, "position": payload.get("database_position")})


def fields(
    ctx: typer.Context,
    table: Annotated[str, typer.Option("--table", help="表 id 或名称。")],
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="数据库 id 或名称；表名传名称时必需（除非已有缺省库）。"
            "数字表 id 可独立使用，无需任何 db。",
        ),
    ] = None,
    include_hidden: IncludeHiddenOpt = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出表字段（含已水合的 FK 目标；position 为物理列序）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    # --db > default_db fallback; a NUMERIC table id still resolves standalone.
    if db is not None:
        db_ref: str | None = db
    elif config.default_db:
        db_ref = str(config.default_db)
    elif table.isdigit():
        db_ref = None
    else:
        raise MbcliError("invalid_args", NO_DB_MESSAGE)
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db_ref) if db_ref is not None else None
        table_id = resolver.table(table, db_id=db_id)
        params = {"include_hidden_fields": "true"} if include_hidden else None
        payload = client.get(f"/api/table/{table_id}/query_metadata", params=params)
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "table query_metadata response is not an object")
    entries = [item for item in payload.get("fields") or [] if isinstance(item, dict)]
    records = [_to_field_info(entry).to_dict() for entry in entries]
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
