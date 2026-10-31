"""`mbcli health`: GET /api/health without credentials (DESIGN §4, §5.1)."""

from __future__ import annotations

from typing import Any

import httpx
import typer

from mbcli.errors import MbcliError
from mbcli.models import HealthInfo
from mbcli.options import state_from_ctx
from mbcli.output import emit_object


def _fetch_version(client: Any) -> str | None:
    """Best-effort version tag from GET /api/session/properties (public)."""
    try:
        properties = client.get("/api/session/properties", auth=False)
    except (MbcliError, httpx.HTTPError):
        return None
    if not isinstance(properties, dict):
        return None
    version = properties.get("version")
    if isinstance(version, dict) and version.get("tag"):
        return str(version["tag"])
    return None


def health(ctx: typer.Context) -> None:
    """实例健康检查（无需凭据）；状态非 ok（含 503 启动中）时 exit 1。"""
    state = state_from_ctx(ctx)
    config = state.load_config()
    with state.build_client(config) as client:
        body = client.get("/api/health", auth=False, pass_status=(503,))
        version = _fetch_version(client)
    if not isinstance(body, dict):
        raise MbcliError("invalid_response", "health response is not an object")
    if body.get("status") != "ok":
        raise MbcliError(
            "unhealthy",
            f"Metabase is not healthy: {body.get('status')!r}",
            detail={"body": body},
        )
    emit_object(HealthInfo(status="ok", version=version))


EPILOG = (
    "示例:\n"
    "  mbcli --url https://metabase.example.com health\n"
    "  mbcli health        # connect 过之后无需任何参数"
)
