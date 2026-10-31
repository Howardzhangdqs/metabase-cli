"""`mbcli whoami`: GET /api/user/current (DESIGN §4)."""

from __future__ import annotations

import typer

from mbcli.errors import MbcliError
from mbcli.models import WhoamiInfo
from mbcli.options import state_from_ctx
from mbcli.output import Format, FormatOpt, OutOpt, emit_object


def whoami(
    ctx: typer.Context,
    format: FormatOpt = Format.json,
    out: OutOpt = None,
) -> None:
    """当前认证用户（校验凭据最快的方式）。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        payload = client.get("/api/user/current")
    if not isinstance(payload, dict):
        raise MbcliError("invalid_response", "/api/user/current response is not an object")
    emit_object(WhoamiInfo.from_api(payload), fmt=format, out=out, no_color=state.no_color)


EPILOG = "示例:\n  mbcli whoami\n  mbcli whoami --format table"
