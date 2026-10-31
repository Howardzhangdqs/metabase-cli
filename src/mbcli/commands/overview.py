"""overview 命令：实例总览（DESIGN §13.2）。

实现注记（v0.2 wave 2，含 2026-10-31 只读实测结论）：

- **库×表数走 include=tables 快路径**：对测试实例（v0.66.0-SNAPSHOT）实测
  `GET /api/database?include=tables` 可用 —— 包裹结构 `{"data":[…],"total":N}`，
  每个库自带完整 `tables[]`（id/name/schema/active/visibility_type，**不含
  fields**，计数足够），单次 ~2.5MB 调用替代 24 次逐库 metadata。仅当服务端
  拒绝该参数（invalid_request / invalid_response / not_found）或返回体里没有任何
  tables 数组时，才回退逐库 `GET /api/database/{id}/metadata`（慢路径，输出标注
  `"slow": true`；认证/网络/5xx 类错误直接上抛，重试无意义不上抛不回退）。
- **根集合不在真实 `/api/collection` 平铺列表里**：根 "Our analytics"
  (id "root") 由本模块合成并置于首位；若服务端列表已含 root 则不重复。
- items 端点的 models 过滤必须是**重复参数**（`models=card&models=dashboard`）；
  逗号连写的单值在 0.66 实测为 400（与 ADR #10 同源的坑）。
- 真实 items 条目**没有 `updated_at`/`collection` 字段**：recents 的
  updated_at 回退 `last-edit-info.timestamp`，collection 由被查询集合自带。
- `table_count` 口径与 `mbcli tables --db` 默认一致（滤掉 inactive 与
  visibility_type 非空的表），agent 两处对数不互相矛盾。
- csv/ndjson 不适用（输出是单一对象，两个记录数组无法并置）：invalid_args
  （exit 2）；可用 json / table / markdown。
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer

from mbcli.client import MetabaseClient
from mbcli.errors import MbcliError
from mbcli.models import CollectionRef
from mbcli.options import state_from_ctx
from mbcli.output import Format, FormatOpt, OutOpt, render_json, render_markdown, render_table

EPILOG = "示例:\n  mbcli overview\n  mbcli overview --top 20 --format markdown"

# §13.2: content distribution covers at most the first COLLECTION_CAP
# non-personal top-level collections; recents take one RECENT_PAGE_SIZE page
# per collection and keep the global --top newest after the merge.
COLLECTION_CAP = 50
RECENT_PAGE_SIZE = 100
RECENT_MODELS = ("card", "dashboard")
ROOT_COLLECTION: dict[str, Any] = {"id": "root", "name": "Our analytics"}

# Server-side signals that `include=tables` is not usable on this instance
# (param rejected / broken shape) -> per-db metadata fallback loop.
_FALLBACK_CODES = frozenset({"invalid_request", "invalid_response", "not_found"})


# -- output dataclasses (local: frozen scope, models.py untouched) ----------------


@dataclass
class _OverviewTotals:
    databases: int
    collections: int
    cards: int
    dashboards: int


@dataclass
class _DatabaseTables:
    id: int | str
    name: str | None
    engine: str | None
    table_count: int


@dataclass
class _CollectionCounters:
    collection: CollectionRef
    cards: int
    dashboards: int


@dataclass
class _RecentItem:
    model: str | None
    id: int | str
    name: str | None
    collection: CollectionRef
    updated_at: str | None


@dataclass
class _Overview:
    totals: _OverviewTotals
    databases: list[_DatabaseTables] = field(default_factory=list)
    collections: list[_CollectionCounters] = field(default_factory=list)
    recent: list[_RecentItem] = field(default_factory=list)
    slow: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        if not self.slow:
            payload.pop("slow")  # fast path: the exact §13.2 shape, no marker
        return payload


# -- section builders --------------------------------------------------------------


def _visible_table(table: dict[str, Any]) -> bool:
    """Same visibility rule as `mbcli tables` by default (no --include-hidden)."""
    return bool(table.get("active", True)) and not table.get("visibility_type")


def _database_rows(client: MetabaseClient) -> tuple[list[_DatabaseTables], bool]:
    """Databases x table_count. Fast path: one `GET /api/database?include=tables`
    (probe-verified on v0.66). Any db arriving without a tables array falls back
    to its own metadata call; the response marks the run as slow then."""
    try:
        payload = client.get("/api/database", params={"include": "tables"})
    except MbcliError as exc:
        if exc.code not in _FALLBACK_CODES:
            raise  # auth/network/server errors: a metadata loop cannot help
        payload = client.get("/api/database")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/database response has no data array")
    rows: list[_DatabaseTables] = []
    slow = False
    for item in data:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        tables = item.get("tables")
        if not isinstance(tables, list):
            metadata = client.get(f"/api/database/{item['id']}/metadata")
            tables = metadata.get("tables") if isinstance(metadata, dict) else None
            if not isinstance(tables, list):
                tables = []
            slow = True
        count = sum(1 for t in tables if isinstance(t, dict) and _visible_table(t))
        rows.append(
            _DatabaseTables(
                id=item["id"], name=item.get("name"), engine=item.get("engine"), table_count=count
            )
        )
    return rows, slow


def _top_level_collections(client: MetabaseClient) -> list[dict[str, Any]]:
    """Non-personal top-level collections (parent_id null), root first, capped.

    The live /api/collection flat list does NOT include the root collection
    (probe-verified): synthesize "Our analytics" (id "root"); a server that
    does list it must not produce a duplicate."""
    payload = client.get("/api/collection")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/collection response is not an array")
    entries = [dict(ROOT_COLLECTION)]
    for item in data:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        if item.get("is_personal") or item.get("parent_id") is not None:
            continue
        if item["id"] == "root":
            continue  # server already listed the root collection: no duplicate
        entries.append({"id": item["id"], "name": item.get("name")})
    return entries[:COLLECTION_CAP]


def _model_total(client: MetabaseClient, collection_id: int | str, model: str) -> int:
    """`total` for one model via a 1-row probe page (2 quick calls per collection)."""
    payload = client.get(f"/api/collection/{collection_id}/items", {"models": [model], "limit": 1})
    total = payload.get("total") if isinstance(payload, dict) else None
    return total if isinstance(total, int) else 0


def _recent_page(client: MetabaseClient, collection_id: int | str) -> list[dict[str, Any]]:
    """One merged card+dashboard items page (repeated models= params, §13.2)."""
    payload = client.get(
        f"/api/collection/{collection_id}/items",
        {"models": list(RECENT_MODELS), "limit": RECENT_PAGE_SIZE},
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    return [item for item in (data or []) if isinstance(item, dict)]


def _item_updated_at(item: dict[str, Any]) -> str | None:
    """Live items carry no updated_at: fall back to last-edit-info.timestamp."""
    updated = item.get("updated_at")
    if isinstance(updated, str) and updated:
        return updated
    last_edit = item.get("last-edit-info")
    if isinstance(last_edit, dict):
        timestamp = last_edit.get("timestamp")
        if isinstance(timestamp, str):
            return timestamp
    return None


# -- output ------------------------------------------------------------------------


def _recent_collection_cell(ref: dict[str, Any]) -> str:
    return str(ref["name"] if ref.get("name") is not None else ref["id"])


def _render_sections(record: dict[str, Any], *, fmt: Format, no_color: bool) -> str:
    """table/markdown: databases and collections as record tables, recent as a
    list; totals (and the slow-path note, when set) lead as plain header lines."""

    def render(header: list[str], rows: list[list[Any]]) -> str:
        if fmt is Format.table:
            return render_table(header, rows, no_color=no_color)
        return render_markdown(header, rows)

    totals = record["totals"]
    parts = [
        (
            "Totals: "
            f"{totals['databases']} databases, {totals['collections']} collections,"
            f" {totals['cards']} cards, {totals['dashboards']} dashboards"
        )
    ]
    if record.get("slow"):
        parts.append("(slow path: include=tables unavailable, per-database metadata fallback)")
    parts.append("Databases")
    parts.append(
        render(
            ["id", "name", "engine", "table_count"],
            [[d["id"], d["name"], d["engine"], d["table_count"]] for d in record["databases"]],
        )
    )
    parts.append("Collections")
    parts.append(
        render(
            ["id", "name", "cards", "dashboards"],
            [
                [c["collection"]["id"], c["collection"]["name"], c["cards"], c["dashboards"]]
                for c in record["collections"]
            ],
        )
    )
    parts.append("Recent")
    parts.append(
        render(
            ["model", "id", "name", "collection", "updated_at"],
            [
                [
                    r["model"],
                    r["id"],
                    r["name"],
                    _recent_collection_cell(r["collection"]),
                    r["updated_at"],
                ]
                for r in record["recent"]
            ],
        )
    )
    return "\n".join(parts) + "\n"


def _finalize(text: str, out: str | None) -> None:
    """stdout, or write to --out and print the summary object (§3.6 semantics)."""
    if out is None:
        if not text.endswith("\n"):
            text += "\n"
        sys.stdout.write(text)
        return
    Path(out).expanduser().write_text(text, encoding="utf-8")
    sys.stdout.write(
        json.dumps({"out": out, "rows": 1, "truncated": False}, ensure_ascii=False) + "\n"
    )


def _emit(record: dict[str, Any], *, fmt: Format, out: str | None, no_color: bool) -> None:
    if fmt is Format.json:
        text = render_json(record)
    elif fmt in (Format.table, Format.markdown):
        text = _render_sections(record, fmt=fmt, no_color=no_color)
    else:
        raise MbcliError(
            "invalid_args",
            f"format '{fmt.value}' is not supported for overview (single object with"
            " two record arrays): use json, table or markdown",
        )
    _finalize(text, out)


# -- command body -------------------------------------------------------------------


def overview(
    ctx: typer.Context,
    top: Annotated[int, typer.Option("--top", min=1, help="最近更新资产条数（默认 10）。")] = 10,
    format: FormatOpt = Format.json,
    out: OutOpt = None,
) -> None:
    """实例总览：库×表数、集合内容分布、最近更新（v0.2 wave 2 实现）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        databases, slow = _database_rows(client)
        collections: list[_CollectionCounters] = []
        recent: list[_RecentItem] = []
        for entry in _top_level_collections(client):
            cards = _model_total(client, entry["id"], "card")
            dashboards = _model_total(client, entry["id"], "dashboard")
            collections.append(
                _CollectionCounters(
                    collection=CollectionRef(id=entry["id"], name=entry["name"]),
                    cards=cards,
                    dashboards=dashboards,
                )
            )
            if cards + dashboards == 0:
                continue  # empty collection: nothing to merge into recents
            for item in _recent_page(client, entry["id"]):
                if item.get("model") not in RECENT_MODELS:
                    continue
                recent.append(
                    _RecentItem(
                        model=item.get("model"),
                        id=item.get("id"),
                        name=item.get("name"),
                        collection=CollectionRef(id=entry["id"], name=entry["name"]),
                        updated_at=_item_updated_at(item),
                    )
                )
    recent.sort(key=lambda item: (item.updated_at is not None, item.updated_at or ""), reverse=True)
    record = _Overview(
        totals=_OverviewTotals(
            databases=len(databases),
            collections=len(collections),
            cards=sum(entry.cards for entry in collections),
            dashboards=sum(entry.dashboards for entry in collections),
        ),
        databases=databases,
        collections=collections,
        recent=recent[:top],
        slow=slow,
    ).to_dict()
    _emit(record, fmt=format, out=out, no_color=state.no_color)
