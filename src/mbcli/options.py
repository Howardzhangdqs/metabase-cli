"""Shared CLI plumbing: root-level state and reusable command-level option types.

Root options (connection/diagnostics) are parsed by the root callback in
cli.py BEFORE the subcommand (DESIGN §3.7); command modules read the resolved
state from the typer context and declare data-shaping options themselves.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated

import typer

from mbcli.client import MetabaseClient
from mbcli.config import Config, load_config

DbRefOpt = Annotated[str, typer.Option("--db", help="数据库 id 或名称。")]
DbHintOpt = Annotated[
    str | None,
    typer.Option("--db", help="数据库 id 或名称（--table 传名称时必填）。"),
]
TableRefOpt = Annotated[str, typer.Option("--table", help="表 id 或名称。")]
IncludeHiddenOpt = Annotated[bool, typer.Option("--include-hidden", help="包含隐藏项。")]
ParamOpt = Annotated[
    list[str] | None,
    typer.Option("--param", help="模板变量参数 k=v（可重复）。"),
]
ModelOpt = Annotated[
    list[str] | None,
    typer.Option("--model", help="资产类型过滤（可重复或逗号分隔）。"),
]


@dataclass
class CliState:
    """Values provided at the root level for one invocation."""

    url: str | None = None
    api_key: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: int | None = None
    verbose: bool = False
    no_color: bool = False

    def load_config(self) -> Config:
        overrides = {
            key: getattr(self, key)
            for key in ("url", "api_key", "username", "password", "timeout")
            if getattr(self, key) is not None
        }
        return load_config(overrides)

    def log(self, message: str) -> None:
        if self.verbose:
            print(message, file=sys.stderr)

    def build_client(self, config: Config | None = None) -> MetabaseClient:
        config = config or self.load_config()
        return MetabaseClient(config, log=self.log if self.verbose else None)


def state_from_ctx(ctx: typer.Context) -> CliState:
    state = ctx.obj
    return state if isinstance(state, CliState) else CliState()
