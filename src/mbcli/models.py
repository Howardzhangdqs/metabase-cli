"""Output dataclasses with stable snake_case field names (DESIGN §5).

Field sets are frozen here (whitelist): unknown upstream fields are dropped,
never passed through, so the CLI contract does not drift with Metabase versions.
`to_dict()` preserves dataclass declaration order (JSON key order contract).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


class _Model:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -- misc / health -----------------------------------------------------------


@dataclass
class WhoamiInfo(_Model):
    id: int
    email: str | None
    is_superuser: bool
    group_ids: list[int] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> WhoamiInfo:
        return cls(
            id=payload.get("id"),
            email=payload.get("email"),
            is_superuser=bool(payload.get("is_superuser", False)),
            group_ids=list(payload.get("group_ids") or []),
        )


@dataclass
class HealthInfo(_Model):
    status: str
    version: str | None = None


# -- metadata (§5.1) -----------------------------------------------------------


@dataclass
class DatabaseInfo(_Model):
    id: int
    name: str
    engine: str | None
    description: str | None
    is_sample: bool
    initial_sync_status: str | None
    timezone: str | None
    # the live instance returns an object ({"flavor","version",...}); pass through as-is
    dbms_version: Any = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> DatabaseInfo:
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            engine=payload.get("engine"),
            description=payload.get("description"),
            is_sample=bool(payload.get("is_sample", False)),
            initial_sync_status=payload.get("initial_sync_status"),
            timezone=payload.get("timezone"),
            dbms_version=payload.get("dbms_version"),
        )


@dataclass
class SchemaInfo(_Model):
    name: str
    table_count: int


@dataclass
class TableInfo(_Model):
    id: int
    schema: str | None
    name: str
    display_name: str | None
    description: str | None
    estimated_row_count: int | None  # approximate; do not rely on it
    active: bool
    field_count: int = 0

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> TableInfo:
        return cls(
            id=payload.get("id"),
            schema=payload.get("schema"),
            name=payload.get("name"),
            display_name=payload.get("display_name"),
            description=payload.get("description"),
            estimated_row_count=payload.get("estimated_row_count"),
            active=bool(payload.get("active", True)),
            field_count=len(payload.get("fields") or []),
        )


@dataclass
class FKTarget(_Model):
    table: str | None
    field: str | None


@dataclass
class FieldInfo(_Model):
    id: int
    name: str
    display_name: str | None
    base_type: str | None
    semantic_type: str | None
    database_type: str | None
    description: str | None
    is_pk: bool
    is_nullable: bool | None
    is_indexed: bool | None
    fk_target: FKTarget | None
    position: int | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> FieldInfo:
        target = payload.get("target")
        fk_target = None
        if isinstance(target, dict):
            table = target.get("table")
            if isinstance(table, dict):
                fk_target = FKTarget(table=table.get("name"), field=target.get("name"))
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            display_name=payload.get("display_name"),
            base_type=payload.get("base_type"),
            semantic_type=payload.get("semantic_type"),
            database_type=payload.get("database_type"),
            description=payload.get("description"),
            is_pk=bool(payload.get("database_is_pk", False)),
            is_nullable=payload.get("database_is_nullable"),
            is_indexed=payload.get("database_indexed"),
            fk_target=fk_target,
            # DESIGN §5.1: position comes from the physical column order
            # (database_position), not the UI override (`position`).
            position=(
                payload["database_position"]
                if payload.get("database_position") is not None
                else payload.get("position")
            ),
        )


@dataclass
class CollectionRef(_Model):
    id: int | str | None
    name: str | None


@dataclass
class CollectionInfo(_Model):
    id: int | str
    name: str
    parent_id: int | str | None
    authority_level: int | None
    is_personal: bool

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> CollectionInfo:
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            parent_id=payload.get("parent_id"),
            authority_level=payload.get("authority_level"),
            is_personal=bool(payload.get("is_personal", False)),
        )


@dataclass
class CollectionItem(_Model):
    id: int | str
    model: str | None
    name: str | None
    description: str | None
    collection: CollectionRef | None
    display: str | None
    updated_at: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> CollectionItem:
        collection = payload.get("collection")
        if not isinstance(collection, dict):
            collection_id = payload.get("collection_id")
            collection = {"id": collection_id} if collection_id is not None else None
        return cls(
            id=payload.get("id"),
            model=payload.get("model"),
            name=payload.get("name"),
            description=payload.get("description"),
            collection=(
                CollectionRef(id=collection["id"], name=collection.get("name"))
                if collection
                else None
            ),
            display=payload.get("display"),
            updated_at=payload.get("updated_at"),
        )


@dataclass
class CollectionListing(_Model):
    total: int | None
    limit: int | None
    offset: int | None
    items: list[CollectionItem] = field(default_factory=list)

    @classmethod
    def from_api(
        cls, payload: dict[str, Any], items: list[CollectionItem] | None = None
    ) -> CollectionListing:
        return cls(
            total=payload.get("total"),
            limit=payload.get("limit"),
            offset=payload.get("offset"),
            items=list(items or []),
        )


@dataclass
class SearchItem(_Model):
    id: int | str
    model: str | None
    name: str | None
    description: str | None
    collection: CollectionRef | None
    database_id: int | None
    updated_at: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> SearchItem:
        return cls(
            id=payload.get("id"),
            model=payload.get("model"),
            name=payload.get("name"),
            description=payload.get("description"),
            collection=(
                CollectionRef(
                    id=payload["collection"]["id"], name=payload["collection"].get("name")
                )
                if isinstance(payload.get("collection"), dict)
                else None
            ),
            database_id=payload.get("database_id"),
            updated_at=payload.get("updated_at"),
        )


# -- query envelope (§5.2) ------------------------------------------------------


@dataclass
class ColumnInfo(_Model):
    name: str
    base_type: str | None
    database_type: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ColumnInfo:
        return cls(
            name=payload.get("name"),
            base_type=payload.get("base_type"),
            database_type=payload.get("database_type"),
        )


@dataclass
class QueryEnvelope(_Model):
    status: str
    row_count: int
    rows_returned: int
    truncated: bool
    columns: list[ColumnInfo] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    running_time_ms: int | None = None
    cached: bool = False
    data_warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_response(cls, payload: dict[str, Any], max_rows: int = 0) -> QueryEnvelope:
        """Client-side truncation contract (§5.2, ADR #12): the server ignores
        request-level limits, so we truncate in memory and report
        truncated/rows_returned explicitly."""
        data = payload.get("data") or {}
        columns = [
            ColumnInfo.from_api(col) for col in (data.get("cols") or []) if isinstance(col, dict)
        ]
        server_rows = data.get("rows") or []
        row_count = len(server_rows)
        if max_rows and max_rows > 0:
            truncated = row_count > max_rows
            out_rows = server_rows[:max_rows]
        else:
            truncated = False
            out_rows = server_rows
        warnings: list[str] = []
        if data.get("rows_truncated"):
            warnings.append("server truncated rows in this response (data.rows_truncated)")
        return cls(
            status=payload.get("status") or "completed",
            row_count=row_count,
            rows_returned=len(out_rows),
            truncated=truncated,
            columns=columns,
            rows=out_rows,
            running_time_ms=payload.get("running_time"),
            cached=bool(payload.get("cached", False)),
            data_warnings=warnings,
        )


# -- cards / dashboards (§5.3) ----------------------------------------------------


@dataclass
class LastEdit(_Model):
    user: str | None
    at: str | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> LastEdit | None:
        if not isinstance(payload, dict):
            return None
        user = " ".join(
            part for part in (payload.get("first_name"), payload.get("last_name")) if part
        )
        return cls(
            user=user or payload.get("email") or payload.get("username"),
            at=payload.get("timestamp"),
        )


@dataclass
class CardDetail(_Model):
    id: int | str
    name: str | None
    description: str | None
    collection: CollectionRef | None
    display: str | None
    query_type: str | None  # "native" | "mbql"
    database_id: int | None
    sql: str | None
    mbql: dict[str, Any] | None
    parameters: list[Any] = field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    last_edit: LastEdit | None = None

    @staticmethod
    def _parse_legacy_query(
        legacy: dict[str, Any],
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        query_type = legacy.get("type")
        sql = None
        mbql = None
        if query_type == "native":
            native = legacy.get("native") or {}
            sql = native.get("query") if isinstance(native, dict) else None
        elif query_type == "query":
            mbql = legacy.get("query")
            query_type = "mbql"
        else:
            return None, None, None
        return query_type, sql, mbql

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> CardDetail:
        dataset_query = payload.get("dataset_query")
        source: dict[str, Any] = dataset_query if isinstance(dataset_query, dict) else {}
        query_type: str | None = None
        sql: str | None = None
        mbql: dict[str, Any] | None = None

        if source.get("lib/type") == "mbql/query":
            # v57+ pMBQL: native SQL lives in the LAST mbql.stage/native stage
            # (probe-verified on card 7876, instance v0.66).
            stages = source.get("stages")
            native_stage = None
            if isinstance(stages, list):
                for stage in stages:
                    if isinstance(stage, dict) and stage.get("lib/type") == "mbql.stage/native":
                        native_stage = stage
            if native_stage is not None:
                query_type, sql = "native", native_stage.get("native")
            else:
                query_type, mbql = "mbql", source
        else:
            query_type, sql, mbql = cls._parse_legacy_query(source)

        if query_type is None:
            # Fallback: `legacy_query` is a JSON-encoded string of the legacy
            # shape on v57+ instances.
            legacy_raw = payload.get("legacy_query")
            if isinstance(legacy_raw, str):
                try:
                    legacy_raw = json.loads(legacy_raw)
                except ValueError:
                    legacy_raw = None
            if isinstance(legacy_raw, dict):
                query_type, sql, mbql = cls._parse_legacy_query(legacy_raw)
                if query_type is not None:
                    source = legacy_raw

        collection = payload.get("collection")
        if not isinstance(collection, dict):
            collection_id = payload.get("collection_id")
            collection = {"id": collection_id} if collection_id is not None else None
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            description=payload.get("description"),
            collection=(
                CollectionRef(id=collection["id"], name=collection.get("name"))
                if collection
                else None
            ),
            display=payload.get("display"),
            query_type=query_type,
            database_id=source.get("database"),
            sql=sql,
            mbql=mbql,
            parameters=list(payload.get("parameters") or []),
            created_at=payload.get("created_at"),
            updated_at=payload.get("updated_at"),
            last_edit=LastEdit.from_api(payload.get("last-edit-info")),
        )


@dataclass
class DashboardTab(_Model):
    id: int | str | None
    name: str | None


@dataclass
class DashboardParameter(_Model):
    id: str | None
    name: str | None
    slug: str | None
    type: str | None
    default: Any = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> DashboardParameter:
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            slug=payload.get("slug"),
            type=payload.get("type"),
            default=payload.get("default"),
        )


@dataclass
class CardPosition(_Model):
    row: int | None
    col: int | None
    size_x: int | None
    size_y: int | None


@dataclass
class DashboardCard(_Model):
    card_id: int | str | None
    name: str | None
    series: list[str] = field(default_factory=list)
    position: CardPosition | None = None
    parameter_mappings: list[Any] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> DashboardCard:
        card = payload.get("card") or {}
        return cls(
            card_id=card.get("id") if isinstance(card, dict) else None,
            name=card.get("name") if isinstance(card, dict) else None,
            series=[s.get("name") for s in (payload.get("series") or []) if isinstance(s, dict)],
            position=CardPosition(
                row=payload.get("row"),
                col=payload.get("col"),
                size_x=payload.get("size_x"),
                size_y=payload.get("size_y"),
            ),
            parameter_mappings=list(payload.get("parameter_mappings") or []),
        )


@dataclass
class DashboardDetail(_Model):
    id: int | str
    name: str | None
    description: str | None
    collection: CollectionRef | None
    tabs: list[DashboardTab] = field(default_factory=list)
    parameters: list[DashboardParameter] = field(default_factory=list)
    cards: list[DashboardCard] = field(default_factory=list)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> DashboardDetail:
        collection = payload.get("collection")
        if not isinstance(collection, dict):
            collection_id = payload.get("collection_id")
            collection = {"id": collection_id} if collection_id is not None else None
        return cls(
            id=payload.get("id"),
            name=payload.get("name"),
            description=payload.get("description"),
            collection=(
                CollectionRef(id=collection["id"], name=collection.get("name"))
                if collection
                else None
            ),
            tabs=[
                DashboardTab(id=tab.get("id"), name=tab.get("name"))
                for tab in (payload.get("tabs") or [])
                if isinstance(tab, dict)
            ],
            parameters=[
                DashboardParameter.from_api(param)
                for param in (payload.get("parameters") or [])
                if isinstance(param, dict)
            ],
            cards=[
                DashboardCard.from_api(dashcard)
                for dashcard in (payload.get("dashcards") or [])
                if isinstance(dashcard, dict)
            ],
        )
