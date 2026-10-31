"""`mbcli tables`: tables of a database (DESIGN §4, §5.1)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.client import MetabaseClient
from mbcli.config import effective_db_ref
from mbcli.errors import MbcliError
from mbcli.models import TableInfo
from mbcli.options import IncludeHiddenOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    "  mbcli tables --db quant_data --match mau\n"
    "  mbcli tables --fields id,name,estimated_row_count --format csv"
)


def _load_metadata(
    resolver: Resolver, client: MetabaseClient, db_id: int, include_hidden: bool
) -> dict[str, Any]:
    """Default path reuses the Resolver's process cache (one metadata GET);
    --include-hidden refetches with include_hidden=true so hidden tables appear."""
    if not include_hidden:
        return resolver._db_metadata(db_id)
    payload = client.get(f"/api/database/{db_id}/metadata", params={"include_hidden": "true"})
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "database metadata response is not an object")
    return payload


def _visible(table: dict[str, Any]) -> bool:
    """Without --include-hidden: drop inactive tables and any non-null visibility_type."""
    return bool(table.get("active", True)) and not table.get("visibility_type")


def tables(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="数据库 id 或名称（缺省回退：已连接的 default_db / MB_DEFAULT_DB）。",
        ),
    ] = None,
    schema: Annotated[str | None, typer.Option("--schema", help="按 schema 名称过滤。")] = None,
    match: Annotated[
        str | None, typer.Option("--match", help="表名子串过滤（不区分大小写）。")
    ] = None,
    include_hidden: IncludeHiddenOpt = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出库内数据表（单次 metadata 调用，进程内缓存；行数为同步估值，仅供参考）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    db_ref = effective_db_ref(db, config)
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db_ref)
        metadata = _load_metadata(resolver, client, db_id, include_hidden)
    entries = [table for table in metadata.get("tables") or [] if isinstance(table, dict)]
    if not include_hidden:
        entries = [table for table in entries if _visible(table)]
    if schema is not None:
        entries = [table for table in entries if table.get("schema") == schema]
    if match is not None:
        needle = match.lower()
        entries = [table for table in entries if needle in str(table.get("name") or "").lower()]
    records = [TableInfo.from_api(table).to_dict() for table in entries]
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
