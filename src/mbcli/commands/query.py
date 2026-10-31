"""`mbcli query`: execute native SQL via POST /api/dataset (DESIGN §4; M2)."""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import uuid4

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


# ISO 8601 date or datetime (2023-07-01 / 2023-07-01T08:00 / 2023-07-01 08:00:30+08:00)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?")


def infer_tag_type(value: str) -> str:
    """Template-tag type from the value's shape (v0.2, live-verified on 47.100.241.247).

    Matrix probed against POST /api/dataset: numeric → "number" (substituted
    unquoted), ISO date/datetime → "date", anything else → "text" (quoted).
    """
    if _ISO_DATE.fullmatch(value.strip()):
        return "date"
    try:
        float(value.strip())
    except ValueError:
        return "text"
    return "number"


def parse_param_items(params: list[str] | None) -> list[tuple[str, str]]:
    """Validate repeatable `--param k=v` options → [(key, value), …] (no I/O).

    The value is passed through verbatim (always a string); `k` may not be
    empty and the `k=v` shape is mandatory (invalid_args, exit 2).
    """
    items: list[tuple[str, str]] = []
    for item in params or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise MbcliError(
                "invalid_args",
                f"invalid --param {item!r}: expected k=v",
                detail={"param": item},
            )
        items.append((key, value))
    return items


def native_param_specs(
    params: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Serialize `--param k=v` into a (parameters, template-tags) pair (v0.2).

    M2 sent only `parameters` with `native.template-tags` pinned empty, which
    the server rejects for any referenced variable: 400
    ``Cannot run the query: missing required parameters: #{"n"}``. Live probing
    (v0.2.0 eval) established the accepted shape — the tag must be DECLARED
    alongside the parameter, each with a stable id:

    - parameters: ``[{"id": <uuid>, "type": "category",
        "target": ["variable", ["template-tag", k]], "value": v}]``
      ("category" matches text/number/date tags alike; "text"/"number"/"date"
      param types are also accepted, "date" alone is not);
    - template-tags: ``{k: {"id": <uuid>, "name": k, "display-name": k,
        "type": "text"|"number"|"date", "required": False}}`` — tag ids are
      REQUIRED by the server (a date tag without id → same 400), separate
      uuid from the parameter id (both probed OK).

    Type is inferred from the value's shape (see infer_tag_type); mbcli never
    rewrites the SQL (ADR #4) — only variable targets, per DESIGN §4.
    """
    parameters: list[dict[str, Any]] = []
    template_tags: dict[str, dict[str, Any]] = {}
    for key, value in parse_param_items(params):
        parameters.append(
            {
                "id": str(uuid4()),
                "type": "category",
                "target": ["variable", ["template-tag", key]],
                "value": value,
            }
        )
        template_tags[key] = {
            "id": str(uuid4()),
            "name": key,
            "display-name": key,
            "type": infer_tag_type(value),
            "required": False,
        }
    return parameters, template_tags


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
    parameters, template_tags = native_param_specs(param)  # cheap validation, no I/O
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
                "native": {"query": native, "template-tags": template_tags},
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
