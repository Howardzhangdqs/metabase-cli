"""`mbcli collections list|show` (DESIGN §4; M3)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.errors import MbcliError
from mbcli.models import CollectionInfo, CollectionItem, CollectionListing
from mbcli.options import ModelOpt, expand_models, state_from_ctx
from mbcli.output import (
    FieldsOpt,
    Format,
    FormatOpt,
    OutOpt,
    emit_object,
    emit_records,
    parse_fields,
    prune_fields,
)
from mbcli.resolver import Resolver

app = typer.Typer(help="浏览集合及其内容。", no_args_is_help=True)


@app.command(
    "list",
    epilog=(
        "示例:\n"
        "  mbcli collections list --fields id,name\n"
        "  mbcli collections list --all --format csv"
    ),
)
def list_collections(
    ctx: typer.Context,
    all: Annotated[bool, typer.Option("--all", help="包含个人集合（默认被过滤掉）。")] = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出集合（默认隐藏个人集合）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        payload = client.get("/api/collection")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/collection response is not an array")
    records: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        info = CollectionInfo.from_api(item)
        if not all and info.is_personal:
            continue
        records.append(info.to_dict())
    emit_records(records, fmt=format, fields=fields, out=out, no_color=state.no_color)


@app.command(
    "show",
    epilog=(
        "示例:\n"
        "  mbcli collections show root --model card --limit 20\n"
        "  mbcli collections show 1039 --model card,dashboard --offset 20"
    ),
)
def show(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="集合 id、名称或保留字 'root'。")],
    model: ModelOpt = None,
    limit: Annotated[int | None, typer.Option("--limit", min=1, help="页大小。")] = None,
    offset: Annotated[int | None, typer.Option("--offset", min=0, help="翻页偏移。")] = None,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """列出集合内容（分页信封 total/limit/offset/items；--model 以复数 models 参数发给服务端）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        collection_id = Resolver(client).collection(ref)
        params: dict[str, Any] = {}
        models = expand_models(model)
        if models:
            params["models"] = models
        if limit is not None:
            params["limit"] = limit
        if offset is not None:
            params["offset"] = offset
        payload = client.get(f"/api/collection/{collection_id}/items", params)
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "collection items response is not an object")
    items = [
        CollectionItem.from_api(item)
        for item in payload.get("data") or []
        if isinstance(item, dict)
    ]
    record = CollectionListing.from_api(payload, items=items).to_dict()
    # §13.4 format audit: json keeps the {total,limit,offset,items} envelope;
    # the tabular formats (csv/ndjson/table/markdown) render the items[] record
    # array itself — with --fields selecting item keys / columns.
    if format is Format.json:
        if fields:
            # --fields prunes the item records only; envelope keys stay intact (§3.6)
            record["items"] = prune_fields(record["items"], parse_fields(fields))
        emit_object(record, fmt=format, out=out, no_color=state.no_color)
    else:
        emit_records(record["items"], fmt=format, fields=fields, out=out, no_color=state.no_color)
