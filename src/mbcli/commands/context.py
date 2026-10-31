"""`mbcli context`: LLM-friendly schema summary (DESIGN §4, §6; M4)."""

from __future__ import annotations

import re
from typing import Annotated, Any

import typer

from mbcli.config import effective_db_ref
from mbcli.errors import MbcliError
from mbcli.models import DatabaseInfo, FieldInfo, FKTarget, TableInfo
from mbcli.options import state_from_ctx
from mbcli.output import render_json
from mbcli.resolver import Resolver

# exact footnote line from DESIGN §6 — the ¹ marks estimated_row_count as approximate
FOOTNOTE = "¹ estimated_row_count 为 Metabase 同步估值，仅作参考"

EPILOG = (
    "示例:\n"
    "  mbcli context --db 15 --table app_mau\n"
    "  mbcli context --db quant_data --max-tables 10 > schema.md\n"
    "  mbcli context --include-fields-pattern '^(uid|id)$' --json"
)


def context(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="数据库 id 或名称（缺省回退：已连接的 default_db / MB_DEFAULT_DB）。",
        ),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option(
            "--table",
            help="限定到指定表（可重复，按给定顺序输出）；缺省=全库按表名字典序取前 N 张。",
        ),
    ] = None,
    max_tables: Annotated[
        int,
        typer.Option("--max-tables", min=1, help="未指定 --table 时按表名排序取前 N 张。"),
    ] = 50,
    include_fields_pattern: Annotated[
        str,
        typer.Option(
            "--include-fields-pattern",
            help="字段名正则过滤（Python re.search 部分匹配语义），防宽表爆炸。",
        ),
    ] = ".*",
    markdown: Annotated[bool, typer.Option("--markdown", help="markdown 输出（默认）。")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="结构化 JSON 输出。")] = False,
) -> None:
    """生成适合直接粘进 LLM prompt 的 schema 摘要（默认 markdown）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    try:
        pattern = re.compile(include_fields_pattern)
    except re.error as exc:
        raise MbcliError("invalid_args", f"invalid --include-fields-pattern: {exc}") from exc
    with state.build_client(config) as client:
        db_id = Resolver(client).db(effective_db_ref(db, config))
        metadata = client.get(f"/api/database/{db_id}/metadata")
    if not isinstance(metadata, dict):
        raise MbcliError("invalid_response", "database metadata response is not an object")
    all_tables = [item for item in metadata.get("tables") or [] if isinstance(item, dict)]
    selected = _select_tables(all_tables, table or [], max_tables)
    # FK columns resolve `fk_target_field_id` against the SAME response (no extra request)
    field_index = _field_index(all_tables)
    if json_output:
        typer.echo(render_json(_json_payload(metadata, selected, pattern, field_index)))
    else:
        typer.echo(_markdown(metadata, selected, pattern, field_index, len(all_tables)))


def _select_tables(
    tables: list[dict[str, Any]], refs: list[str], max_tables: int
) -> list[dict[str, Any]]:
    """Named tables must all resolve (not_found / ambiguous_ref, like the
    resolver); without --table, take the first N tables in name order
    (deterministic, DESIGN §4)."""
    if not refs:
        return sorted(tables, key=lambda t: str(t.get("name") or ""))[:max_tables]
    selected: list[dict[str, Any]] = []
    for ref in refs:
        if ref.isdigit():
            matches = [t for t in tables if str(t.get("id")) == ref]
        else:
            matches = [t for t in tables if t.get("name") == ref]
        if not matches:
            raise MbcliError(
                "not_found",
                f"table {ref!r} not found",
                detail={"kind": "table", "ref": ref},
            )
        if len(matches) > 1:
            raise MbcliError(
                "ambiguous_ref",
                f"table name {ref!r} matches {len(matches)} candidates; use the numeric id instead",
                detail={
                    "kind": "table",
                    "ref": ref,
                    "candidates": [{"id": t.get("id"), "name": t.get("name")} for t in matches],
                },
            )
        selected.append(matches[0])
    return selected


def _field_index(tables: list[dict[str, Any]]) -> dict[Any, tuple[str | None, str | None]]:
    """field id -> (table name, field name) across the whole metadata response."""
    index: dict[Any, tuple[str | None, str | None]] = {}
    for table in tables:
        table_name = table.get("name")
        for field in table.get("fields") or []:
            if isinstance(field, dict) and field.get("id") is not None:
                index[field["id"]] = (str(table_name), field.get("name"))
    return index


def _included_fields(table: dict[str, Any], pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    fields = [f for f in table.get("fields") or [] if isinstance(f, dict)]
    return [f for f in fields if pattern.search(str(f.get("name") or ""))]


def _fk_label(field: dict[str, Any], field_index: dict[Any, tuple[str | None, str | None]]) -> str:
    fk_id = field.get("fk_target_field_id")
    if fk_id is None or fk_id not in field_index:
        return ""
    table_name, field_name = field_index[fk_id]
    return f"{table_name}.{field_name}"


def _markdown(
    metadata: dict[str, Any],
    tables: list[dict[str, Any]],
    pattern: re.Pattern[str],
    field_index: dict[Any, tuple[str | None, str | None]],
    total: int,
) -> str:
    """Markdown rendering, exact layout per DESIGN §6."""
    lines = [
        (
            f"# Database: {metadata.get('name')} ({metadata.get('engine')},"
            f" id={metadata.get('id')}) — {len(tables)} tables shown / {total} total"
        )
    ]
    for table in tables:
        rows = table.get("estimated_row_count")
        row_label = rows if rows is not None else "?"
        lines.append("")
        lines.append(f"## Table `{table.get('name')}` (id={table.get('id')}, ~{row_label} rows¹)")
        description = table.get("description")
        if description:
            lines.append(f"> {description}")
        lines.append("")
        lines.append("| column | type | semantic | pk | fk → | description |")
        lines.append("|---|---|---|---|---|---|")
        for field in _included_fields(table, pattern):
            lines.append(_field_row(field, field_index))
    lines.append("")
    lines.append(FOOTNOTE)
    return "\n".join(lines)


def _field_row(field: dict[str, Any], field_index: dict[Any, tuple[str | None, str | None]]) -> str:
    # markdown row per DESIGN §6: empty cells render as `| |` (single space),
    # non-empty cells as `| value |`
    cells = [
        field.get("name") or "",
        field.get("database_type") or "",
        # semantic column: semantic_type, falling back to base_type (§6 example)
        field.get("semantic_type") or field.get("base_type") or "",
        "PK" if field.get("database_is_pk") else "",
        _fk_label(field, field_index),
        field.get("description") or "",
    ]
    return "|" + "|".join(f" {cell} " if cell else " " for cell in cells) + "|"


def _json_payload(
    metadata: dict[str, Any],
    tables: list[dict[str, Any]],
    pattern: re.Pattern[str],
    field_index: dict[Any, tuple[str | None, str | None]],
) -> dict[str, Any]:
    """`--json` output: {"database":{…},"tables":[{…,"fields":[…]}]} (§5.1 shapes)."""
    out_tables: list[dict[str, Any]] = []
    for table in tables:
        record = TableInfo.from_api(table).to_dict()
        fields: list[dict[str, Any]] = []
        for field in _included_fields(table, pattern):
            info = FieldInfo.from_api(field)
            fk_id = field.get("fk_target_field_id")
            if fk_id in field_index:  # back-resolved within the same response
                target_table, target_field = field_index[fk_id]
                info.fk_target = FKTarget(table=target_table, field=target_field)
            fields.append(info.to_dict())
        record["field_count"] = len(fields)
        record["fields"] = fields
        out_tables.append(record)
    return {"database": DatabaseInfo.from_api(metadata).to_dict(), "tables": out_tables}
