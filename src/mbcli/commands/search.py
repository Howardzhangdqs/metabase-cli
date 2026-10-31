"""`mbcli search`: global search (DESIGN §4; M3)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.errors import MbcliError
from mbcli.models import SearchItem
from mbcli.options import ModelOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n  mbcli search 数据 --limit 3\n  mbcli search 收入 --model card,dashboard --limit 10"
)


def expand_models(model: list[str] | None) -> list[str]:
    """Flatten repeatable / comma-separated --model values.

    Sent as the repeatable `models` (plural) query param — the singular `model=`
    is silently ignored by Metabase (probe-verified, ADR #10).
    """
    values: list[str] = []
    for entry in model or []:
        values.extend(part.strip() for part in entry.split(",") if part.strip())
    return values


def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="检索关键词。")],
    model: ModelOpt = None,
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="库 id 或名称；仅对当前返回页做客户端过滤"
            "（card 匹配 database_id，table 匹配 table_db_id）。",
        ),
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", min=1, help="页大小（服务端默认 50）。")
    ] = None,
    offset: Annotated[int | None, typer.Option("--offset", min=0, help="翻页偏移。")] = None,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """全局检索资产（卡片/仪表盘/集合/表），Agent 定位资产的入口。

    服务端 search 不支持按库过滤：--db 是对返回页的客户端过滤，超出 --limit
    的结果不会扫到；按库找表请用 `mbcli tables --db D --match`。
    """
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db) if db is not None else None
        params: dict[str, Any] = {"q": query}
        models = expand_models(model)
        if models:
            params["models"] = models
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        payload = client.get("/api/search", params)
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/search response has no data array")
    items = [item for item in data if isinstance(item, dict)]
    if db_id is not None:
        # page-local client filter: cards carry `database_id`, table-model items
        # carry `table_db_id`; items with neither key cannot match a database.
        items = [
            item
            for item in items
            if item.get("database_id") == db_id or item.get("table_db_id") == db_id
        ]
    records = [SearchItem.from_api(item).to_dict() for item in items]
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)
