"""`mbcli search`: global search (DESIGN §4; §13.4 enhancements).

v0.2 additions:
- `--match-any`: the QUERY positional is split on commas; every keyword is
  fetched on its own (paged) and the pools are merged, deduped by (model, id)
  in first-seen order — the server has no OR-search, so this is client-side
  (ADR #21).
- `--limit 0`: fetch the FULL result set (client-side paging, page size 100,
  hard cap 1000 rows). Hitting the cap annotates the output as truncated
  (§5.2 philosophy: json envelope key / trailing sentinel).
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli.client import MetabaseClient
from mbcli.errors import MbcliError
from mbcli.models import SearchItem
from mbcli.options import ModelOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt, emit_records
from mbcli.resolver import Resolver

# fetch-all contract (§13.4): client paging, page size 100, hard cap 1000 rows
FETCH_PAGE_SIZE = 100
FETCH_HARD_CAP = 1000

EPILOG = (
    "示例:\n"
    "  mbcli search 数据 --limit 3\n"
    "  mbcli search 收入 --model card,dashboard --limit 10\n"
    "  mbcli search 手机,mobile,phone --match-any --limit 5   # 多关键词并集去重\n"
    "  mbcli search 用户 --limit 0                            # 拉全集（页 100，上限 1000）"
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


def split_keywords(query: str) -> list[str]:
    """Split a `--match-any` QUERY into non-empty comma-separated keywords."""
    return [part.strip() for part in query.split(",") if part.strip()]


def _extract_data(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/search response has no data array")
    return [item for item in data if isinstance(item, dict)]


def _matches_db(item: dict[str, Any], db_id: int) -> bool:
    # page-local client filter: cards carry `database_id`, table-model items
    # carry `table_db_id`; items with neither key cannot match a database.
    return item.get("database_id") == db_id or item.get("table_db_id") == db_id


def _fetch_keyword_pool(
    client: MetabaseClient,
    keyword: str,
    models: list[str],
    db_id: int | None,
    start_offset: int,
    stop_after: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Paged fetch for one keyword until the pool reaches `stop_after` items
    (post-db-filter) or the result set is exhausted.

    Returns (items, exhausted). exhausted=False means the budget stopped us
    while full pages kept coming — the full set may be larger. The offset
    advances by the number of rows actually returned, so servers that clamp
    the page size below the requested one do not cause rows to be skipped.
    """
    pool: list[dict[str, Any]] = []
    offset = start_offset
    while True:
        page_size = min(FETCH_PAGE_SIZE, stop_after)
        params: dict[str, Any] = {"q": keyword, "limit": page_size, "offset": offset}
        if models:
            params["models"] = models
        payload = client.get("/api/search", params)
        data = _extract_data(payload)
        pool.extend(item for item in data if db_id is None or _matches_db(item, db_id))
        total = payload.get("total") if isinstance(payload, dict) else None
        offset += len(data)
        end_reached = (
            not data or len(data) < page_size or (isinstance(total, int) and offset >= total)
        )
        if end_reached:
            return pool, True
        if len(pool) >= stop_after:
            return pool, False  # budget reached; more may exist


def search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="检索关键词；--match-any 时为逗号分隔多关键词。")],
    model: ModelOpt = None,
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="库 id 或名称；仅对返回页做客户端过滤"
            "（card 匹配 database_id，table 匹配 table_db_id）。",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            min=0,
            help="页大小（服务端默认 50）；0 = 拉全集（客户端翻页，页 100，"
            f"硬上限 {FETCH_HARD_CAP} 行，超限在输出尾标注 truncated）。",
        ),
    ] = None,
    offset: Annotated[int | None, typer.Option("--offset", min=0, help="翻页偏移。")] = None,
    match_any: Annotated[
        bool,
        typer.Option(
            "--match-any",
            help="把 QUERY 视为逗号分隔多关键词：各取结果后按 (model,id) 去重合并。",
        ),
    ] = False,
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
    if match_any:
        keywords = split_keywords(query)
        if not keywords:
            raise MbcliError("invalid_args", "--match-any needs at least one non-empty keyword")
    else:
        keywords = [query]
    start_offset = offset or 0
    with state.build_client(config) as client:
        resolver = Resolver(client)
        db_id = resolver.db(db) if db is not None else None
        models = expand_models(model)
        merged: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any]] = set()
        fetch_truncated = False

        if not match_any and limit != 0:
            # single-page path — request params and page-local semantics stay
            # exactly as the v0.1 contract (DESIGN §4: --db filters this page)
            params: dict[str, Any] = {"q": query}
            if models:
                params["models"] = models
            if limit is not None:
                params["limit"] = limit
            if offset is not None:
                params["offset"] = offset
            payload = client.get("/api/search", params)
            items = _extract_data(payload)
            if db_id is not None:
                items = [item for item in items if _matches_db(item, db_id)]
            merged = items
        else:
            # --match-any merge and/or --limit 0 fetch-all (§13.4): paged fetch
            # per keyword, dedupe by (model, id) in first-seen order. With an
            # explicit --limit N>0 the merged output is capped at N — the top-N
            # of a merged dedup can only contain items from the first N of each
            # keyword's own list, so the per-keyword budget is N (bounded by
            # the hard cap; hitting the cap annotates the output as truncated).
            display_cap = limit if (limit is not None and limit > 0) else None
            budget = min(display_cap, FETCH_HARD_CAP) if display_cap else FETCH_HARD_CAP
            for keyword in keywords:
                pool, _exhausted = _fetch_keyword_pool(
                    client, keyword, models, db_id, start_offset, budget
                )
                for item in pool:
                    key = (item.get("model"), item.get("id"))
                    if key not in seen:
                        seen.add(key)
                        merged.append(item)
                if len(merged) >= FETCH_HARD_CAP:
                    # hard cap reached — always annotate. Even when the server
                    # reported total == 1000 and every page came back, a
                    # server-side total cap (observed live: broad queries
                    # report exactly 1000) makes "complete" indistinguishable
                    # from "at least 1000", so agents must be warned.
                    fetch_truncated = True
                    merged = merged[:FETCH_HARD_CAP]
                    break

        records = [SearchItem.from_api(item).to_dict() for item in merged]
    if match_any and limit is not None and limit > 0:
        records = records[:limit]  # display cap over the merged, deduped pool
    emit_records(
        records,
        fmt=format,
        fields=fields,
        out=out,
        truncated=fetch_truncated,
        no_color=state.no_color,
    )
