"""Name resolution (DESIGN §3.5): `<id>` or `<name>` refs for db/table/collection.

Lookups use process-lifetime caches: one `GET /api/database` for the db index,
one `GET /api/database/{id}/metadata` per database, one `GET /api/collection`
for collections. Numeric refs are passed through without any request.
"""

from __future__ import annotations

from typing import Any

from mbcli.client import MetabaseClient
from mbcli.errors import MbcliError


def _is_id(ref: str) -> bool:
    return ref.isdigit()


class Resolver:
    def __init__(self, client: MetabaseClient) -> None:
        self._client = client
        self._db_index: dict[str, list[dict[str, Any]]] | None = None
        self._db_id_index: dict[int, str] | None = None
        self._metadata: dict[int, dict[str, Any]] = {}
        self._collection_index: dict[str, list[dict[str, Any]]] | None = None

    # -- databases ----------------------------------------------------------

    def db(self, ref: str) -> int:
        """Resolve a database ref to its id."""
        if _is_id(ref):
            return int(ref)
        return self._pick(self._db_name_index().get(ref, []), "database", ref)

    def db_name_map(self) -> dict[int, str]:
        """id → database name from the cached `GET /api/database` index.

        Public read-only accessor (Round-1 eval P2-1: `search` decorates items
        with database_name at zero extra request cost — the index is fetched at
        most once per Resolver and shared with every db-name resolution).
        """
        self._db_name_index()  # ensure the index is loaded
        assert self._db_id_index is not None
        return dict(self._db_id_index)

    def _db_name_index(self) -> dict[str, list[dict[str, Any]]]:
        if self._db_index is None:
            payload = self._client.get("/api/database")
            data = payload.get("data") if isinstance(payload, dict) else payload
            index: dict[str, list[dict[str, Any]]] = {}
            id_index: dict[int, str] = {}
            for item in data or []:
                if isinstance(item, dict) and item.get("name") is not None:
                    index.setdefault(str(item["name"]), []).append(
                        {"id": item.get("id"), "name": item.get("name")}
                    )
                    if isinstance(item.get("id"), int):
                        id_index[item["id"]] = str(item["name"])
            self._db_index = index
            self._db_id_index = id_index
        return self._db_index

    # -- tables --------------------------------------------------------------

    def table(self, ref: str, db_id: int | str | None = None) -> int:
        """Resolve a table ref to its id. A table NAME requires db_id."""
        if _is_id(ref):
            return int(ref)
        if db_id is None:
            raise MbcliError(
                "invalid_args",
                "resolving a table by name requires --db (or pass a numeric table id)",
            )
        db_id = int(db_id)
        metadata = self._db_metadata(db_id)
        tables = metadata.get("tables") or []
        matches = [
            {"id": table.get("id"), "name": table.get("name")}
            for table in tables
            if isinstance(table, dict) and table.get("name") == ref
        ]
        return self._pick(matches, "table", ref)

    def _db_metadata(self, db_id: int) -> dict[str, Any]:
        if db_id not in self._metadata:
            payload = self._client.get(f"/api/database/{db_id}/metadata")
            if not isinstance(payload, dict):
                raise MbcliError("invalid_response", "database metadata response is not an object")
            self._metadata[db_id] = payload
        return self._metadata[db_id]

    # -- collections -----------------------------------------------------------

    def collection(self, ref: str) -> int | str:
        """Resolve a collection ref; the reserved name 'root' maps to 'root'."""
        if ref == "root":
            return "root"
        if _is_id(ref):
            return int(ref)
        return self._pick(self._collection_name_index().get(ref, []), "collection", ref)

    def _collection_name_index(self) -> dict[str, list[dict[str, Any]]]:
        if self._collection_index is None:
            payload = self._client.get("/api/collection")
            items = payload.get("data") if isinstance(payload, dict) else payload
            index: dict[str, list[dict[str, Any]]] = {}
            for item in items or []:
                if isinstance(item, dict) and item.get("name") is not None:
                    index.setdefault(str(item["name"]), []).append(
                        {"id": item.get("id"), "name": item.get("name")}
                    )
            self._collection_index = index
        return self._collection_index

    # -- shared -----------------------------------------------------------------

    @staticmethod
    def _pick(matches: list[dict[str, Any]], kind: str, ref: str) -> int:
        if not matches:
            raise MbcliError(
                "not_found",
                f"{kind} {ref!r} not found",
                detail={"kind": kind, "ref": ref},
            )
        if len(matches) > 1:
            raise MbcliError(
                "ambiguous_ref",
                f"{kind} name {ref!r} matches {len(matches)} candidates;"
                " use the numeric id instead",
                detail={"kind": kind, "ref": ref, "candidates": matches},
            )
        return int(matches[0]["id"])  # type: ignore[arg-type]
