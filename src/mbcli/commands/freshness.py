"""freshness 命令：每表时间列的 min/max 数据新鲜度（DESIGN §13.1）。

v0.2 wave 2 实现主体；本模块先承接 wave 1 注册的最终签名。
"""

from __future__ import annotations

from typing import Annotated

import typer

from mbcli.errors import MbcliError
from mbcli.output import FieldsOpt, Format, FormatOpt, OutOpt

EPILOG = (
    "示例:\n"
    "  mbcli freshness --db quant_data\n"
    "  mbcli freshness --db 15 --table app_mau --table stock_daily\n"
    "  mbcli freshness --db 15 --sample --max-tables 20   # 无指纹表显式采样"
)


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
