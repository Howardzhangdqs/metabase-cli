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
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt

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


# -- v0.2 stubs (DESIGN §13): signatures are final, bodies land in wave 2 ------
#
# Registered in wave 1 so agents can depend on the option grammar; every body
# fails fast with not_implemented (exit 1) instead of a usage error, so a
# missing capability is distinguishable from a malformed invocation.

FRESHNESS_EPILOG = (
    "示例:\n"
    "  mbcli freshness --db quant_data\n"
    "  mbcli freshness --db 15 --table app_mau --table stock_daily\n"
    "  mbcli freshness --db 15 --sample --max-tables 20   # 无指纹表显式采样"
)


@app.command("freshness", epilog=FRESHNESS_EPILOG)
def freshness(
    ctx: typer.Context,
    db: Annotated[
        str | None,
        typer.Option("--db", help="数据库 id 或名称（缺省回退：default_db / MB_DEFAULT_DB）。"),
    ] = None,
    table: Annotated[
        list[str] | None,
        typer.Option("--table", help="限定表（id 或名称，可重复）；缺省=全库前 N 张。"),
    ] = None,
    max_tables: Annotated[
        int,
        typer.Option("--max-tables", min=1, help="处理表数上限（默认 20，防查询风暴）。"),
    ] = 20,
    sample: Annotated[
        bool,
        typer.Option(
            "--sample", help="无指纹的表显式发 MIN/MAX 采样查询（默认只读指纹，零查询）。"
        ),
    ] = False,
    format: FormatOpt = Format.json,
    fields: FieldsOpt = None,
    out: OutOpt = None,
) -> None:
    """每表时间列的 min/max 数据新鲜度（优先读字段指纹，零查询；v0.2 wave 2 实现）。"""
    raise MbcliError(
        "not_implemented", "freshness is registered but not implemented yet (v0.2 wave 2)"
    )


OVERVIEW_EPILOG = "示例:\n  mbcli overview\n  mbcli overview --top 20 --format markdown"


@app.command("overview", epilog=OVERVIEW_EPILOG)
def overview(
    ctx: typer.Context,
    top: Annotated[int, typer.Option("--top", min=1, help="最近更新资产条数（默认 10）。")] = 10,
    format: FormatOpt = Format.json,
    out: OutOpt = None,
) -> None:
    """实例总览：库×表数、集合内容分布、最近更新（v0.2 wave 2 实现）。"""
    raise MbcliError(
        "not_implemented", "overview is registered but not implemented yet (v0.2 wave 2)"
    )


BATCH_EPILOG = (
    "示例:\n  printf 'tables --db 15 --match fut\\n# 注释行\\nfields --table 3258\\n' | mbcli batch"
)


@app.command("batch", epilog=BATCH_EPILOG)
def batch(ctx: typer.Context) -> None:
    """从 stdin 逐行读取子命令 argv，单进程批量执行（NDJSON 输出；v0.2 wave 3 实现）。"""
    raise MbcliError("not_implemented", "batch is registered but not implemented yet (v0.2 wave 3)")


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
