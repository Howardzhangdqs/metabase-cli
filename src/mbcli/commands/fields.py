"""`mbcli fields`: fields of a table (DESIGN §4, §5.1) — plus the v0.2
cross-table column search (DESIGN §13.4).

Two modes, exactly one required:
- `--table <id|name> [--db D]`: single-table field list from
  `GET /api/table/{id}/query_metadata` (hydrated FK targets).
- `--search <substr> --db D`: scan EVERY table's fields in the db metadata
  (Resolver-cached, zero extra requests beyond that one metadata GET) and
  emit `[{"table","table_id","column","base_type","semantic_type","is_pk",
  "fk_target","reserved"}]`. `reserved` flags MySQL 8.0 reserved-word column
  names so agents know to backtick them in generated SQL (ADR #21/#22).
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.config import NO_DB_MESSAGE, effective_db_ref
from mbcli.errors import MbcliError
from mbcli.models import FieldInfo
from mbcli.options import IncludeHiddenOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.reserved import is_reserved
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    "  mbcli fields --table app_mau --db quant_data\n"
    "  mbcli fields --table 3258        # 数字表 id 无需 --db\n"
    "  mbcli fields --search year_month --db 15   # 跨表列搜索（零额外请求）"
)


def _to_field_info(payload: dict[str, Any]) -> FieldInfo:
    """§5.1 `position` reports the physical column ordinal, which query_metadata
    carries as `database_position` (its `position` is the UI ordering override),
    so normalize the key before the whitelist mapping in models.FieldInfo."""
    return FieldInfo.from_api({**payload, "position": payload.get("database_position")})


def _field_index(
    tables: list[dict[str, Any]],
) -> dict[Any, tuple[str | None, str | None]]:
    """field id -> (table name, field name) across the whole metadata response;
    used to back-resolve `fk_target_field_id` with zero extra requests."""
    index: dict[Any, tuple[str | None, str | None]] = {}
    for table in tables:
        table_name = table.get("name")
        for field in table.get("fields") or []:
            if isinstance(field, dict) and field.get("id") is not None:
                index[field["id"]] = (str(table_name), field.get("name"))
    return index


def _visible(table: dict[str, Any]) -> bool:
    """Without --include-hidden: drop inactive tables and non-null visibility_type."""
    return bool(table.get("active", True)) and not table.get("visibility_type")


def _column_search_records(
    metadata: dict[str, Any], needle: str, include_hidden: bool
) -> list[dict[str, Any]]:
    """§13.4 cross-table column search rows, in metadata order (deterministic)."""
    lowered = needle.lower()
    tables = [item for item in metadata.get("tables") or [] if isinstance(item, dict)]
    index = _field_index(tables)
    records: list[dict[str, Any]] = []
    for table in tables:
        if not include_hidden and not _visible(table):
            continue
        for field in table.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "")
            if lowered not in name.lower():
                continue
            fk_target: dict[str, Any] | None = None
            fk_id = field.get("fk_target_field_id")
            if fk_id in index:
                target_table, target_field = index[fk_id]
                fk_target = {"table": target_table, "field": target_field}
            records.append(
                {
                    "table": table.get("name"),
                    "table_id": table.get("id"),
                    "column": field.get("name"),
                    "base_type": field.get("base_type"),
                    "semantic_type": field.get("semantic_type"),
                    "is_pk": bool(field.get("database_is_pk", False)),
                    "fk_target": fk_target,
                    "reserved": is_reserved(name),
                }
            )
    return records


def fields(
    ctx: typer.Context,
    table: Annotated[
        str | None,
        typer.Option("--table", help="表 id 或名称（与 --search 互斥）。"),
    ] = None,
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="数据库 id 或名称；表名/列搜索必填（或已有缺省库）。"
            "数字表 id 可独立使用，无需任何 db。",
        ),
    ] = None,
    search: Annotated[
        str | None,
        typer.Option(
            "--search",
            help="跨表列名子串搜索（需 --db；扫 metadata 全表字段，零额外请求）。",
        ),
    ] = None,
    include_hidden: IncludeHiddenOpt = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出表字段（含已水合的 FK 目标；position 为物理列序）或跨表搜索列名。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    if search is not None and table is not None:
        raise MbcliError("invalid_args", "--search and --table are mutually exclusive")
    if search is None and table is None:
        raise MbcliError("invalid_args", "exactly one of --table or --search is required")
    if search is not None:
        if not search.strip():
            raise MbcliError("invalid_args", "--search must be a non-empty substring")
        # column search is scoped to one database (name or id; no default-free
        # numeric shortcut like --table <id>)
        db_ref = effective_db_ref(db, config)
        with state.build_client(config) as client:
            resolver = Resolver(client)
            db_id = resolver.db(db_ref)
            metadata = resolver._db_metadata(db_id)
        records = _column_search_records(metadata, search.strip(), include_hidden)
        emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
        return
    # single-table mode
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
