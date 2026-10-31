"""`mbcli connect` / `mbcli disconnect`: connection persistence (DESIGN §3.1
连接持久化, §4 连接管理, ADR #18).

`connect` validates the credentials against the live instance (login + whoami
in password mode, whoami in api-key mode) BEFORE anything is written, then
persists the [metabase] section (0600, atomic) and invalidates the session
cache. Overwriting a DIFFERENT existing connection requires --force; reconnecting
the same one (e.g. after a password change) does not. Passwords are stored in
PLAIN TEXT — the README says so and recommends API keys.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from mbcli.config import (
    Config,
    load_config,
    parse_toml,
    remove_metabase_section,
    save_metabase_section,
)
from mbcli.errors import MbcliError
from mbcli.options import state_from_ctx
from mbcli.output import render_json


def connect(
    ctx: typer.Context,
    url: Annotated[str | None, typer.Option("--url", help="Metabase 实例根地址。")] = None,
    username: Annotated[str | None, typer.Option("--username", help="登录用户名。")] = None,
    password: Annotated[
        str | None,
        typer.Option("--password", help="登录密码（缺失时隐藏输入补问；建议用 env 而非 argv）。"),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="API Key；优先于用户名/密码。"),
    ] = None,
    default_db: Annotated[
        str | None,
        typer.Option("--default-db", help="保存为 --db 缺省库（id 或名称，经校验后以 id 落盘）。"),
    ] = None,
    non_interactive: Annotated[
        bool,
        typer.Option(
            "--non-interactive",
            help="绝不交互补问；缺任何字段直接 exit 2（agent/脚本路径）。",
        ),
    ] = False,
    force: Annotated[bool, typer.Option("--force", help="允许覆盖一个不同的已有连接。")] = False,
) -> None:
    """校验凭据并持久化连接到 config.toml（之后所有命令零连接参数）。

    逐字段取值：CLI 参数 > MB_* 环境变量 > 交互式补问（仅当前两者皆缺才提示，
    密码隐藏输入）。先验证（密码模式登录+whoami / API Key 模式 whoami）后落盘：
    任何失败不写任何内容；成功会连带废弃 session 缓存。覆盖不同的旧连接需
    --force，同一连接重连（如改密码）无需。
    """
    state = state_from_ctx(ctx)
    overrides = {
        key: getattr(state, key)
        for key in ("url", "api_key", "username", "password", "timeout")
        if getattr(state, key) is not None
    }
    # lenient load: the URL may be missing here (connect prompts for it);
    # lower-priority sources still contribute username/password/api_key/paths.
    base = load_config(overrides, require_url=False)

    # Mode intent: connect's own credential flags decide the mode, and values of
    # the OTHER mode from lower-priority sources are ignored — switching from a
    # saved api-key connection to username/password (or vice versa) never needs
    # manual file editing.
    if api_key is not None:
        key_mode = True
        username = password = None
    elif username is not None or password is not None:
        key_mode = False
        api_key = None
        username = username or base.username
        password = password or base.password
    elif base.api_key:
        key_mode, api_key = True, base.api_key
    else:
        key_mode = False
        username, password = base.username, base.password
    url = url or base.url

    missing = []
    if not url:
        missing.append("--url")
    if key_mode:
        if not api_key:
            missing.append("--api-key")
    else:
        if not username:
            missing.append("--username")
        if not password:
            missing.append("--password")
    if non_interactive and missing:
        raise MbcliError(
            "invalid_args",
            "connect: missing " + ", ".join(missing) + " (--non-interactive never prompts;"
            " pass the flags or set MB_URL/MB_USERNAME/MB_PASSWORD/MB_API_KEY)",
        )
    if not url:
        url = typer.prompt("Metabase URL")
    if not key_mode:
        username = username or typer.prompt("Username")
        password = password or typer.prompt("Password", hide_input=True)
    elif not api_key:
        api_key = typer.prompt("API key", hide_input=True)
    url = url.rstrip("/")

    # VALIDATE BEFORE SAVE: password mode -> login + whoami; key mode -> whoami.
    validation_config = Config(
        url=url,
        api_key=api_key if key_mode else None,
        username=None if key_mode else username,
        password=None if key_mode else password,
        timeout=base.timeout,
        session_cache=base.session_cache,
    )
    with state.build_client(validation_config) as client:
        user = client.get("/api/user/current")
        db_info = _resolve_default_db(client, default_db) if default_db else None
    if not isinstance(user, dict):
        raise MbcliError("invalid_response", "/api/user/current response is not an object")

    # Overwrite protection: a DIFFERENT connection (url or identity) needs --force;
    # the same connection may reconnect freely (e.g. after a password change).
    existing = parse_toml(base.config_file)
    old_url = existing.get("url")
    old_identity = existing.get("api_key") or existing.get("username")
    was_connected = bool(old_url and old_identity)
    if was_connected and not force:
        same_connection = old_url == url and (
            (api_key and existing.get("api_key") == api_key)
            or (
                not api_key and not existing.get("api_key") and existing.get("username") == username
            )
        )
        if not same_connection:
            raise MbcliError(
                "invalid_args",
                f"config file already holds a different connection ({old_url});"
                " re-run with --force to replace it",
                detail={"config_file": str(base.config_file), "existing_url": old_url},
            )

    values: dict[str, Any] = {"url": url}
    if key_mode:
        values["api_key"] = api_key
    else:
        values["username"] = username
        values["password"] = password
    if db_info is not None:
        values["default_db"] = str(db_info["id"])  # store the id: resolves deterministically
    save_metabase_section(base.config_file, values)
    _unlink(base.session_cache)  # the old session belongs to the old connection

    typer.echo(
        render_json(
            {
                "connected": True,
                "url": url,
                "user": {"id": user.get("id"), "email": user.get("email")},
                "default_db": db_info,
                "replaced": old_url if was_connected else None,
                "config_file": str(Path(base.config_file).expanduser()),
            }
        )
    )


def disconnect(ctx: typer.Context) -> None:
    """忘记已保存的连接：删除 config.toml 的 \\[metabase] 段与 session 缓存；幂等，恒 exit 0。"""
    config = load_config(None, require_url=False)
    config_file = Path(config.config_file).expanduser()
    existing = parse_toml(config_file)
    was_connected = bool(
        existing.get("url") and (existing.get("api_key") or existing.get("username"))
    )
    remove_metabase_section(config_file)
    cache_path = Path(config.session_cache).expanduser()
    cache_cleared = cache_path.is_file()
    if cache_cleared:
        _unlink(cache_path)
    typer.echo(
        render_json(
            {
                "disconnected": True,
                "was_connected": was_connected,
                "session_cache_cleared": cache_cleared,
            }
        )
    )


EPILOG = (
    "示例:\n"
    "  mbcli connect --url https://metabase.example.com --username me@example.com"
    " --default-db quant_data\n"
    "  MB_URL=… MB_USERNAME=… MB_PASSWORD=… mbcli connect --non-interactive"
    "   # 全靠环境变量\n"
    "  mbcli connect --password 新密码        # 同一连接改密重连，无需 --force"
)

DISCONNECT_EPILOG = "示例:\n  mbcli disconnect\n  MB_CONFIG_FILE=/tmp/other.toml mbcli disconnect   # 操作另一份配置文件"


# -- helpers ---------------------------------------------------------------------------


def _resolve_default_db(client: Any, ref: str) -> dict[str, Any]:
    """Validate --default-db against GET /api/database (existence is checked for
    ids too) and return {"id":…,"name":…}."""
    payload = client.get("/api/database")
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise MbcliError("invalid_response", "/api/database response has no data array")
    if ref.isdigit():
        db_id = int(ref)
        for item in data:
            if isinstance(item, dict) and item.get("id") == db_id:
                return {"id": db_id, "name": item.get("name")}
        raise MbcliError(
            "not_found",
            f"database id {ref} not found",
            detail={"kind": "database", "ref": ref},
        )
    matches = [item for item in data if isinstance(item, dict) and item.get("name") == ref]
    if not matches:
        raise MbcliError(
            "not_found",
            f"database {ref!r} not found",
            detail={"kind": "database", "ref": ref},
        )
    if len(matches) > 1:
        raise MbcliError(
            "ambiguous_ref",
            f"database name {ref!r} matches {len(matches)} candidates; use the numeric id",
            detail={
                "kind": "database",
                "ref": ref,
                "candidates": [{"id": m.get("id"), "name": m.get("name")} for m in matches],
            },
        )
    return {"id": matches[0].get("id"), "name": ref}


def _unlink(path: str | Path) -> None:
    """Best-effort file removal (missing files are fine; never fatal)."""
    try:
        Path(path).expanduser().unlink(missing_ok=True)
    except OSError:
        pass
