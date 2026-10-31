"""overview 命令：实例总览（DESIGN §13.2）。

v0.2 wave 2 实现主体；本模块先承接 wave 1 注册的最终签名。
"""

from __future__ import annotations

from typing import Annotated

import typer

from mbcli.errors import MbcliError
from mbcli.output import Format, FormatOpt, OutOpt

EPILOG = "示例:\n  mbcli overview\n  mbcli overview --top 20 --format markdown"


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
