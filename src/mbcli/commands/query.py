"""`mbcli query`: execute native SQL via POST /api/dataset (DESIGN §4; M2)."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from mbcli import guard
from mbcli.config import effective_db_ref
from mbcli.errors import MbcliError
from mbcli.models import QueryEnvelope
from mbcli.options import ParamOpt, state_from_ctx
from mbcli.output import FieldsOpt, Format, FormatOpt, MaxRowsOpt, OutOpt, emit_envelope
from mbcli.resolver import Resolver

EPILOG = (
    "示例:\n"
    '  mbcli query --db 15 --native "SELECT 1 AS one"\n'
    '  mbcli query --db quant_data --native "SELECT * FROM app_mau LIMIT 3"'
    " --format csv --max-rows 100\n"
    '  mbcli query --native "SELECT count(*) FROM app_mau WHERE date={{dt}}"'
    " --param dt=2023-07-01"
)


def native_parameters(params: list[str] | None) -> list[dict[str, Any]]:
    """Serialize repeatable `--param k=v` options into dataset parameters.

    M2 scope (DESIGN §4): native template VARIABLES only — every param maps to
    {"type":"category","target":["variable",["template-tag",k]],"value":v}.
    The value is passed through verbatim (always a string); `k` may not be
    empty and the `k=v` shape is mandatory (invalid_args, exit 2).
    """
    parameters: list[dict[str, Any]] = []
    for item in params or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise MbcliError(
                "invalid_args",
                f"invalid --param {item!r}: expected k=v",
                detail={"param": item},
            )
        parameters.append(
            {"type": "category", "target": ["variable", ["template-tag", key]], "value": value}
        )
    return parameters


def envelope_from_payload(payload: Any, max_rows: int) -> QueryEnvelope:
    """Model a /api/dataset-style response with client-side truncation (§5.2)."""
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "dataset response is not an object")
    return QueryEnvelope.from_response(payload, max_rows=max_rows)


def query(
    ctx: typer.Context,
    native: Annotated[
        str,
        typer.Option(
            "--native",
            help="要执行的 SQL；非 SELECT 语句默认拒绝（写保护先于任何网络请求执行）。",
        ),
    ],
    db: Annotated[
        str | None,
        typer.Option(
            "--db",
            help="数据库 id 或名称（缺省回退：已连接的 default_db / MB_DEFAULT_DB）。",
        ),
    ] = None,
    param: ParamOpt = None,
    allow_write: Annotated[
        bool,
        typer.Option(
            "--allow-write",
            help="绕过只读写保护（确认语句安全后才使用）。",
        ),
    ] = False,
    format: FormatOpt = Format.json,
    max_rows: MaxRowsOpt = 1000,
    out: OutOpt = None,
    fields: FieldsOpt = None,
) -> None:
    """执行原生 SQL（默认只读；服务端忽略请求级限量，--max-rows 为客户端截断）。"""
    guard.check_readonly(native, allow_write)  # before ANY config/network work (§3.4)
    parameters = native_parameters(param)  # cheap validation, no I/O
    state = state_from_ctx(ctx)
    config = state.load_config()
    db_ref = effective_db_ref(db, config)
    with state.build_client(config) as client:
        db_id = Resolver(client).db(db_ref)
        payload = client.post_json(
            "/api/dataset",
            {
                "database": db_id,
                "type": "native",
                "native": {"query": native, "template-tags": {}},
                "parameters": parameters,
            },
        )
    emit_envelope(
        envelope_from_payload(payload, max_rows),
        fmt=format,
        fields=fields,
        out=out,
        no_color=state.no_color,
    )
