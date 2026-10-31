"""`mbcli schemas`: distinct schemas of a database (DESIGN §4, §5.1)."""

from __future__ import annotations

from collections import Counter
from typing import Annotated

import typer

from mbcli.config import effective_db_ref
from mbcli.models import SchemaInfo
from mbcli.options import state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    "  mbcli schemas --db quant_data\n"
    "  mbcli schemas        # 使用 connect --default-db 保存的缺省库"
)


DB_HELP = "数据库 id 或名称（缺省回退：已连接的 default_db / MB_DEFAULT_DB）。"


def schemas(
    ctx: typer.Context,
    db: Annotated[str | None, typer.Option("--db", help=DB_HELP)] = None,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出数据库的全部 schema（MySQL 通常仅有 ''）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    db_ref = effective_db_ref(db, config)
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db_ref)
        # Single GET /api/database/{id}/metadata, reused from the Resolver's
        # process cache (DESIGN §0: one call returns the full tables[] list).
        metadata = resolver._db_metadata(db_id)
    counts: Counter[str] = Counter()
    for table in metadata.get("tables") or []:
        if isinstance(table, dict):
            counts[str(table.get("schema") or "")] += 1
    records = [
        SchemaInfo(name=name, table_count=count).to_dict() for name, count in sorted(counts.items())
    ]
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
