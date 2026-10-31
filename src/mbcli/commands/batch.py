"""batch 命令：stdin 逐行子命令，单进程批量执行（DESIGN §13.3）。

v0.2 wave 2 实现主体；本模块先承接 wave 1 注册的最终签名。
"""

from __future__ import annotations

import typer

from mbcli.errors import MbcliError

EPILOG = (
    "示例:\n  printf 'tables --db 15 --match fut\\n# 注释行\\nfields --table 3258\\n' | mbcli batch"
)


def batch(ctx: typer.Context) -> None:
    """从 stdin 逐行读取子命令 argv，单进程批量执行（NDJSON 输出；v0.2 wave 3 实现）。"""
    raise MbcliError("not_implemented", "batch is registered but not implemented yet (v0.2 wave 3)")
