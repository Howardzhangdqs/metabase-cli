"""freshness 命令：每表时间列的 min/max 数据新鲜度（DESIGN §13.1）。

v0.2 wave 2 实现主体；本模块先承接 wave 1 注册的最终签名。

实测指纹形态（v0.66，db 15，2026-10-31 只读探测）：时间列的 min/max 嵌在
`fingerprint.type["type/DateTime"].{earliest,latest}`（ISO 字符串）；491 个字段
fingerprint 为 null、88 个只有 {"global":{…}}。DESIGN 预设的扁平
`{"min":…,"max":…}` 变体解析上同样容忍（先真实形态、后容忍变体）。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Annotated, Any

import typer

from mbcli.config import effective_db_ref
from mbcli.errors import MbcliError
from mbcli.options import state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    "  mbcli freshness --db quant_data\n"
    "  mbcli freshness --db 15 --table app_mau --table stock_daily\n"
    "  mbcli freshness --db 15 --sample --max-tables 20   # 无指纹表显式采样"
)

# Time-column priority tiers (DESIGN §13.1): semantic type > temporal base type
# > name regex; ties break on the lowest database_position (fallback: UI
# position, per ADR #17). Lower tier number wins.
_TEMPORAL_SEMANTIC_TYPES = frozenset(
    {"type/CreationTimestamp", "type/UpdatedAt", "type/CreationTime"}
)
_TEMPORAL_BASE_TYPES = frozenset({"type/DateTime", "type/Date"})
_NAME_RE = re.compile(r"(^|_)(date|dt|day|month|year|time)$", re.IGNORECASE)

# Sub-fingerprint keys that may carry temporal bounds, most specific first.
_TEMPORAL_FINGERPRINT_KEYS = ("type/DateTime", "type/Date", "type/Time")


@dataclass
class FreshnessRow:
    """One output record; declaration order is the JSON key-order contract."""

    table: str | None
    table_id: int | None
    column: str | None
    min: Any
    max: Any
    source: str  # "fingerprint" | "sample" | "none"
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _field_position(field: dict[str, Any]) -> float:
    """database_position (physical column order), falling back to UI position."""
    for key in ("database_position", "position"):
        value = field.get(key)
        if value is not None:
            return float(value)
    return float("inf")


def _tier(field: dict[str, Any]) -> int:
    if field.get("semantic_type") in _TEMPORAL_SEMANTIC_TYPES:
        return 0
    if field.get("base_type") in _TEMPORAL_BASE_TYPES:
        return 1
    if _NAME_RE.search(str(field.get("name") or "")):
        return 2
    return 3


def _pick_time_column(fields: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Deterministic one-column-per-table pick (DESIGN §13.1); ties keep the
    first field in metadata order (stable sort)."""
    ranked = [(field, _tier(field)) for field in fields]
    ranked = [item for item in ranked if item[1] < 3]
    if not ranked:
        return None
    return min(ranked, key=lambda item: (item[1], _field_position(item[0])))[0]


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...] | list[str]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _fingerprint_bounds(field: dict[str, Any]) -> tuple[Any, Any]:
    """(min, max) from a field fingerprint, tolerant of every observed shape.

    Live v0.66 nests temporal bounds at type["type/DateTime"].{earliest,latest};
    older/hypothetical variants (flat {"min","max"} at the top, min/max inside
    the typed sub-dict, non-temporal Number min/max) are parsed too. Both bounds
    missing/unusable -> (None, None), i.e. "no fingerprint" for this column.
    """
    fingerprint = field.get("fingerprint")
    if not isinstance(fingerprint, dict):
        return None, None
    if "min" in fingerprint or "max" in fingerprint:  # flat variant
        return fingerprint.get("min"), fingerprint.get("max")
    typed = fingerprint.get("type")
    if not isinstance(typed, dict):
        return None, None
    for key in _TEMPORAL_FINGERPRINT_KEYS:
        sub = typed.get(key)
        if isinstance(sub, dict):
            low = _first_present(sub, ("earliest", "min"))
            high = _first_present(sub, ("latest", "max"))
            if low is not None or high is not None:
                return low, high
    # non-temporal typed fingerprints (e.g. type/Number) carry min/max directly
    for sub in typed.values():
        if isinstance(sub, dict) and (sub.get("min") is not None or sub.get("max") is not None):
            return sub.get("min"), sub.get("max")
    return None, None


def _quote_ident(identifier: str) -> str:
    """Always-backtick an identifier; embedded backticks are doubled."""
    return "`" + identifier.replace("`", "``") + "`"


def _sample_sql(table: dict[str, Any], field: dict[str, Any]) -> str:
    column = _quote_ident(str(field.get("name") or ""))
    table_ref = _quote_ident(str(table.get("name") or ""))
    schema = table.get("schema")
    if schema:
        table_ref = f"{_quote_ident(str(schema))}.{table_ref}"
    return f"SELECT MIN({column}), MAX({column}) FROM {table_ref}"


def _select_tables(
    tables: list[dict[str, Any]], refs: list[str], max_tables: int
) -> list[dict[str, Any]]:
    """Named tables must all resolve (not_found / ambiguous_ref); without
    --table, the first N tables in name order (deterministic, DESIGN §13.1)."""
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


def _sample_bounds(payload: Any) -> tuple[Any, Any]:
    """First row of a /api/dataset response -> (min, max); empty result -> nulls."""
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("rows") if isinstance(data, dict) else None
    row = rows[0] if rows else []
    low = row[0] if len(row) > 0 else None
    high = row[1] if len(row) > 1 else None
    return low, high


def _freshness_row(client: Any, db_id: int, table: dict[str, Any], sample: bool) -> FreshnessRow:
    name = str(table.get("name"))
    columns = [f for f in table.get("fields") or [] if isinstance(f, dict)]
    column = _pick_time_column(columns)
    if column is None:
        return FreshnessRow(
            table=name,
            table_id=table.get("id"),
            column=None,
            min=None,
            max=None,
            source="none",
            reason="no time column",
        )
    column_name = column.get("name")
    low, high = _fingerprint_bounds(column)
    if low is not None or high is not None:
        return FreshnessRow(
            table=name,
            table_id=table.get("id"),
            column=column_name,
            min=low,
            max=high,
            source="fingerprint",
            reason=None,
        )
    if not sample:
        return FreshnessRow(
            table=name,
            table_id=table.get("id"),
            column=column_name,
            min=None,
            max=None,
            source="none",
            reason="no fingerprint (use --sample)",
        )
    payload = client.post_json(
        "/api/dataset",
        {
            "database": db_id,
            "type": "native",
            "native": {"query": _sample_sql(table, column), "template-tags": {}},
            "parameters": [],
        },
    )
    low, high = _sample_bounds(payload)
    return FreshnessRow(
        table=name,
        table_id=table.get("id"),
        column=column_name,
        min=low,
        max=high,
        source="sample",
        reason=None,
    )


def freshness(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option("--db", help="数据库 id 或名称（缺省回退：default_db / MB_DEFAULT_DB）。"),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option("--table", help="限定表（id 或名称，可重复）；缺省=全库前 N 张。"),
    ] = None,
    max_tables: Annotated[
        int,
        typer.Option("--max-tables", min=1, help="处理表数上限（默认 20，防查询风暴）。"),
    ] = 20,
    sample: Annotated[
        bool,
        typer.Option(
            "--sample", help="无指纹的表显式发 MIN/MAX 采样查询（默认只读指纹，零查询）。"
        ),
    ] = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """每表时间列的 min/max 数据新鲜度（优先读字段指纹，零查询；v0.2 wave 2 实现）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    db_ref = effective_db_ref(db, config)
    refs = table or []
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db_ref)
        metadata = resolver._db_metadata(db_id)  # single metadata GET, cached
        entries = [t for t in metadata.get("tables") or [] if isinstance(t, dict)]
        selected = _select_tables(entries, refs, max_tables)
        truncated = not refs and len(entries) > len(selected)
        records = [_freshness_row(client, db_id, t, sample).to_dict() for t in selected]
    emit_records(
        records,
        fmt=format,
        fields=fields,
        out=out,
        truncated=truncated,
        no_color=state.no_color,
    )
