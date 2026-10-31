"""batch 命令：stdin 逐行子命令，单进程批量执行（DESIGN §13.3）。

实现注记（v0.2 wave 3）：

- 每行经 shlex.split 得到完整子命令 argv，**进程内**重放 `mbcli.cli:main`
  （redirect_stdout/redirect_stderr 捕获后归一为 NDJSON 行）——一个进程、
  一次 uv 启动；session 磁盘缓存跨行天然共享（首行登录落盘，后续行直接
  复用，不再发 POST /api/session）。
- **根级选项不透传**：每行都是一次全新解析的完整 argv，外层
  `mbcli --url … batch` 的根级选项对行内命令不可见（进程内每次调用重建
  CliState）；行内可自带根级选项（如 `--url http://x databases list`），
  env（MB_*）/.env/config.toml 对每行照常生效。
- 行内嵌套 `batch` 一律拒绝（该行 invalid_args、exit 2），不影响其余行；
  `#` 注释行与空白行跳过（不计 index）。
- stdout：每条**已执行**的命令一行 `{"index","argv","exit","error","result"}`
  （index 只对已执行命令计数；result 为该命令 stdout 可解析为 JSON 时的
  对象/数组，否则为原始字符串——如 `--format csv`；error 取该命令 stderr
  单行错误 JSON 的 error 对象）。
- 进程退出码：全部行成功 0，任一行非 0 则 1；单行错误绝不中断整批。
"""

from __future__ import annotations

import contextlib
import io
import json
import shlex
import sys
from typing import Any

import typer

from mbcli.errors import MbcliError

EPILOG = (
    "示例:\n  printf 'tables --db 15 --match fut\\n# 注释行\\nfields --table 3258\\n' | mbcli batch"
)


def _error_object(error: MbcliError) -> dict[str, Any]:
    """Reuse the §3.3 stderr contract to build a line's `error` value."""
    return json.loads(error.to_stderr_json())["error"]


def _stderr_error_object(stderr_text: str) -> dict[str, Any] | None:
    """Last parseable `{"error":{…}}` line from a command's captured stderr
    (-v request logs are not JSON and are skipped); None when there is none."""
    for line in reversed(stderr_text.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            return payload["error"]
        return None
    return None


def _result_value(stdout_text: str) -> Any:
    """The inner command's stdout: parsed JSON when it parses, else the raw
    string (e.g. `--format csv` / `--format table`); empty output -> None."""
    if not stdout_text.strip():
        return None
    try:
        return json.loads(stdout_text)
    except ValueError:
        return stdout_text


def _execute(argv: list[str]) -> dict[str, Any]:
    """Run one subcommand argv IN-PROCESS via mbcli.cli:main — the real entry
    point, so the MbcliError/TyperException -> stderr-JSON / exit-code contract
    is identical to a standalone invocation. A line failure never propagates."""
    from mbcli.cli import main as mbcli_main  # deferred: mbcli.cli imports this module

    out_buf, err_buf = io.StringIO(), io.StringIO()
    exit_code = 0
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            mbcli_main(list(argv))
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):  # bool is an int subclass; int() anyway
            exit_code = int(code)
        else:
            exit_code = 1  # sys.exit("message") style: treat as failure
    except Exception as exc:  # noqa: BLE001 — line isolation is the contract
        exit_code = 1
        internal = MbcliError("invalid_response", f"unexpected failure: {exc}")
        err_buf.write(internal.to_stderr_json() + "\n")
    return {
        "exit": exit_code,
        "error": _stderr_error_object(err_buf.getvalue()),
        "result": _result_value(out_buf.getvalue()),
    }


def batch(ctx: typer.Context) -> None:
    """从 stdin 逐行读取子命令 argv，单进程批量执行（NDJSON 输出；v0.2 wave 3 实现）。"""
    failures = 0
    index = 0
    for raw in sys.stdin.read().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        index += 1
        try:
            argv = shlex.split(line)
        except ValueError as exc:
            argv = [line]
            outcome = {
                "exit": 2,
                "error": _error_object(
                    MbcliError("invalid_args", f"shlex parse failed: {exc}", detail={"line": line})
                ),
                "result": None,
            }
        else:
            if argv and argv[0] == "batch":
                outcome = {
                    "exit": 2,
                    "error": _error_object(
                        MbcliError(
                            "invalid_args",
                            "nested 'batch' is not allowed inside batch lines",
                            detail={"line": line},
                        )
                    ),
                    "result": None,
                }
            else:
                outcome = _execute(argv)
        entry: dict[str, Any] = {"index": index, "argv": argv}
        entry.update(outcome)
        sys.stdout.write(json.dumps(entry, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        if entry["exit"] != 0:
            failures += 1
    if failures:
        raise typer.Exit(1)
