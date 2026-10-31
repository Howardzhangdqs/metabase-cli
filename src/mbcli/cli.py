"""mbcli entry point: typer app, root-level options, error -> exit-code mapping.

Exit-code contract: 0 success, 1 API/execution errors, 2 usage/argument errors.
Errors are emitted as single-line JSON on stderr (DESIGN §3.3).
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from mbcli.errors import MbcliError
from mbcli.options import CliState

app = typer.Typer(
    name="mbcli",
    help="面向 Agent 的 Metabase 命令行客户端：stdout 只输出数据（默认 JSON），诊断与错误走 stderr。",
    epilog=(
        "选项文法：连接/诊断选项（--url/--api-key/--username/--password/--timeout/--no-color/-v）"
        "必须放在子命令之前；数据塑形选项（--format/--fields/--max-rows/--out 及各命令专属选项）"
        "放在子命令之后。\n"
        '退出码：0 成功；1 API/执行错误（stderr 单行 JSON，形如 {"error":{"code":"…"}}）；'
        "2 用法错误（改参数即可修复）。\n"
        "完整使用文档见 USAGE.md。"
    ),
    add_completion=False,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    url: Annotated[
        str | None, typer.Option("--url", help="Metabase 实例根地址（env: MB_URL）。")
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option("--api-key", help="Metabase API Key，优先于账密登录（env: MB_API_KEY）。"),
    ] = None,
    username: Annotated[
        str | None, typer.Option("--username", help="登录用户名（env: MB_USERNAME）。")
    ] = None,
    password: Annotated[
        str | None,
        typer.Option(
            "--password", help="登录密码；仅限交互调试，推荐 env 传入（env: MB_PASSWORD）。"
        ),
    ] = None,
    timeout: Annotated[
        int | None,
        typer.Option("--timeout", min=1, help="单请求 read 超时秒数，默认 60（env: MB_TIMEOUT）。"),
    ] = None,
    no_color: Annotated[bool, typer.Option("--no-color", help="关闭彩色输出。")] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="请求日志输出到 stderr（凭据已脱敏）。"),
    ] = False,
) -> None:
    """连接与诊断选项必须放在子命令之前（数据塑形选项放在子命令之后）。"""
    ctx.obj = CliState(
        url=url,
        api_key=api_key,
        username=username,
        password=password,
        timeout=timeout,
        verbose=verbose,
        no_color=no_color,
    )
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


# Command modules are imported after the callback definition: cli.py owns
# registration, commands only depend on core modules (no import cycle).
from mbcli.commands import (
    cards,
    collections,
    connect,
    context,
    dashboards,
    databases,
    fields,
    health,
    query,
    schemas,
    search,
    tables,
    whoami,
)

app.command("health", epilog=health.EPILOG)(health.health)
app.command("whoami", epilog=whoami.EPILOG)(whoami.whoami)
app.command("connect", epilog=connect.EPILOG)(connect.connect)
app.command("disconnect", epilog=connect.DISCONNECT_EPILOG)(connect.disconnect)
app.add_typer(databases.app, name="databases", help="浏览数据库。")
app.command("schemas", epilog=schemas.EPILOG)(schemas.schemas)
app.command("tables", epilog=tables.EPILOG)(tables.tables)
app.command("fields", epilog=fields.EPILOG)(fields.fields)
app.command("query", epilog=query.EPILOG)(query.query)
app.add_typer(cards.app, name="cards", help="执行或查看已保存的问题（卡片）。")
app.add_typer(collections.app, name="collections", help="浏览集合及其内容。")
app.add_typer(dashboards.app, name="dashboards", help="查看仪表盘。")
app.command("search", epilog=search.EPILOG)(search.search)
app.command("context", epilog=context.EPILOG)(context.context)


def main(argv: list[str] | None = None) -> None:
    """Console-script entry point. Translates exceptions into the exit contract."""
    try:
        code = app(args=argv, standalone_mode=False)
        sys.exit(int(code or 0))
    except typer.TyperException as exc:
        # Usage/parameter errors from the CLI layer -> invalid_args (exit 2).
        if type(exc).__name__ == "NoArgsIsHelpError":  # bare `mbcli databases` etc.
            typer.echo(str(exc.format_message()))
            error = MbcliError("invalid_args", "no subcommand given; run with --help")
        else:
            message = " ".join(str(exc.format_message()).split())
            error = MbcliError("invalid_args", message, detail={"cli_error": message})
        error.emit()
        sys.exit(error.exit_code)
    except MbcliError as exc:
        exc.emit()
        sys.exit(exc.exit_code)
    except (typer.Abort, KeyboardInterrupt):
        sys.stderr.write('{"error":{"code":"aborted","message":"aborted by user","detail":{}}}\n')
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
