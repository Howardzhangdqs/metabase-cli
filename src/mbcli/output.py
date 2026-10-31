"""Output shaping (DESIGN §3.6): json / ndjson / csv / table, --fields pruning,
--max-rows truncation, --out file writing with stdout summary.

stdout carries data only; json.dumps uses ensure_ascii=False and keys keep
dataclass declaration order. --fields prunes record keys (or CSV columns) only —
never the query envelope (§5.2).
"""

from __future__ import annotations

import csv
import io
import json
import sys
from collections.abc import Sequence
from dataclasses import is_dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table as RichTable

from mbcli.errors import MbcliError


class Format(str, Enum):
    json = "json"
    ndjson = "ndjson"
    csv = "csv"
    table = "table"


# Shared command-level data-shaping option types (DESIGN §3.7).
FormatOpt = Annotated[
    Format,
    typer.Option("--format", help="输出格式：json（默认）/ ndjson / csv / table。"),
]
FieldsOpt = Annotated[
    str | None,
    typer.Option(
        "--fields",
        help="逗号分隔的记录键白名单（仅裁剪记录/CSV 列，绝不裁剪查询信封）。",
    ),
]
MaxRowsOpt = Annotated[
    int,
    typer.Option(
        "--max-rows",
        min=0,
        help="客户端行数上限，0 = 不限；截断经 truncated/rows_returned 上报。",
    ),
]
OutOpt = Annotated[
    str | None,
    typer.Option("--out", help="结果写入 FILE；stdout 变为摘要对象。"),
]


def to_jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(value.to_dict())
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    return value


def render_json(data: Any) -> str:
    return json.dumps(to_jsonable(data), ensure_ascii=False)


def parse_fields(fields: str | None) -> list[str] | None:
    if not fields:
        return None
    return [item.strip() for item in fields.split(",") if item.strip()]


def prune_fields(
    records: list[dict[str, Any]], fields: Sequence[str] | None
) -> list[dict[str, Any]]:
    """Prune record keys; a misspelled field is invalid_args (§3.6)."""
    if not fields:
        return records
    if records:
        known: set[str] = set()
        for record in records:
            known.update(record.keys())
        unknown = [name for name in fields if name not in known]
        if unknown:
            raise MbcliError(
                "invalid_args",
                "unknown --fields: " + ", ".join(unknown),
                detail={"unknown": unknown, "available": sorted(known)},
            )
    return [{name: record.get(name) for name in fields} for record in records]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool | int | float | str):
        return value
    return json.dumps(to_jsonable(value), ensure_ascii=False)


def render_csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """RFC 4180 CSV: quote comma/quote/newline fields, double embedded quotes."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect="excel", lineterminator="\r\n")
    writer.writerow(list(header))
    for row in rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


def render_ndjson(envelope: dict[str, Any]) -> str:
    """Envelope header line, one row per line, trailing sentinel (§5.2)."""
    lines = [
        json.dumps({"columns": to_jsonable(envelope.get("columns") or [])}, ensure_ascii=False)
    ]
    for row in envelope.get("rows") or []:
        lines.append(json.dumps(to_jsonable(row), ensure_ascii=False))
    lines.append(
        json.dumps(
            {
                "truncated": bool(envelope.get("truncated")),
                "rows_returned": envelope.get("rows_returned"),
            },
            ensure_ascii=False,
        )
    )
    return "\n".join(lines) + "\n"


def render_table(
    header: Sequence[str], rows: Sequence[Sequence[Any]], no_color: bool = False
) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, no_color=no_color, highlight=False)
    table = RichTable()
    for column in header:
        table.add_column(str(column))
    for row in rows:
        table.add_row(*[str(_cell(value)) for value in row])
    console.print(table)
    return buffer.getvalue()


def _write_stdout(text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    sys.stdout.write(text)


def _finalize(text: str, out: str | None, rows: int, truncated: bool) -> None:
    if out is None:
        _write_stdout(text)
        return
    Path(out).expanduser().write_text(text, encoding="utf-8")
    summary = {"out": str(out), "rows": rows, "truncated": bool(truncated)}
    _write_stdout(json.dumps(summary, ensure_ascii=False))


def emit_records(
    records: list[dict[str, Any]],
    *,
    fmt: Format = Format.json,
    fields: str | None = None,
    out: str | None = None,
    truncated: bool = False,
    no_color: bool = False,
) -> None:
    field_list = parse_fields(fields)
    pruned = prune_fields(records, field_list)
    if field_list:
        header = list(field_list)
    elif pruned:
        header = list(pruned[0].keys())
    else:
        header = []
    rows = [[record.get(name) for name in header] for record in pruned]
    if fmt is Format.json:
        text = render_json(pruned)
    elif fmt is Format.csv:
        text = render_csv(header, rows)
    elif fmt is Format.ndjson:
        text = "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in pruned)
    elif fmt is Format.table:
        text = render_table(header, rows, no_color=no_color)
    else:  # pragma: no cover - enum exhaustiveness
        raise MbcliError("invalid_args", f"unsupported format: {fmt}")
    _finalize(text, out, rows=len(pruned), truncated=truncated)


def emit_envelope(
    envelope: Any,
    *,
    fmt: Format = Format.json,
    fields: str | None = None,
    out: str | None = None,
    no_color: bool = False,
) -> None:
    env = to_jsonable(envelope)
    if not isinstance(env, dict):
        raise MbcliError("invalid_response", "query envelope is not an object")
    if fields:
        raise MbcliError(
            "invalid_args",
            "--fields cannot be applied to a query envelope (envelope keys are never pruned)",
        )
    header = [str(column.get("name")) for column in env.get("columns") or []]
    rows = env.get("rows") or []
    if fmt is Format.json:
        text = render_json(env)
    elif fmt is Format.csv:
        text = render_csv(header, rows)
    elif fmt is Format.ndjson:
        text = render_ndjson(env)
    elif fmt is Format.table:
        text = render_table(header, rows, no_color=no_color)
    else:  # pragma: no cover
        raise MbcliError("invalid_args", f"unsupported format: {fmt}")
    _finalize(
        text, out, rows=int(env.get("rows_returned") or 0), truncated=bool(env.get("truncated"))
    )


def emit_object(
    obj: Any,
    *,
    fmt: Format = Format.json,
    out: str | None = None,
    no_color: bool = False,
) -> None:
    """Single-object output (health, whoami): json or table only."""
    data = to_jsonable(obj)
    if fmt is Format.table:
        rows = [[key, _cell(value)] for key, value in dict(data).items()]
        text = render_table(["key", "value"], rows, no_color=no_color)
    elif fmt is Format.json:
        text = render_json(data)
    else:
        raise MbcliError(
            "invalid_args",
            f"format '{fmt.value}' is not supported for single-object output; use json or table",
        )
    _finalize(text, out, rows=1, truncated=False)
