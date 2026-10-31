#!/usr/bin/env python3
"""Amalgamate src/mbcli/ into the single-file mbcli.py (stdlib only).

The layered source tree stays canonical and fully tested; mbcli.py at the
repo root is a *generated* artifact for machines that cannot install the
package but have their own Python >= 3.10 with typer + httpx available.
Dependencies are NOT bundled — the script uses whatever the target machine
provides.

Transform per module (merged in dependency order; src has no import cycles):
  - strip the module docstring and `from __future__ import annotations`
    (exactly ONE future import is emitted at the merged top);
  - drop intra-package imports entirely (`mbcli.*` / relative) — the bound
    names already exist in the merged flat namespace by ordering;
  - hoist plain top-level external imports to the merged header, deduped
    (from-imports merge per source module; NESTED imports such as the
    tomllib try/except in config.py stay in place untouched);
  - emit a tiny namespace shim after each section (`_ns` is
    types.SimpleNamespace) so attribute-style references keep working:
    cli.py registers commands via `health.EPILOG` / `databases.app`, and
    commands/query.py calls `guard.check_readonly`;
  - drop any `if __name__ == "__main__":` guard; a single one is re-emitted
    at the merged tail.

Self-checks (any failure exits 1):
  1. the generated source parses with ast.parse;
  2. zero occurrences of `from mbcli` / `import mbcli` / `from .` anywhere
     in the generated file;
  3. `mbcli.py --help` exits 0 under the current interpreter.

Output is deterministic: fixed module order, sorted shim names, and the
header timestamp comes from SOURCE_DATE_EPOCH when set, otherwise from the
newest source mtime (stable across back-to-back regenerations).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "mbcli"
OUT = REPO / "mbcli.py"

# Dependency order (layered architecture, DESIGN.md §3): leaf modules first,
# commands/*.py in registration order, cli.py last (it imports all commands).
MODULES = [
    "errors",
    "config",
    "auth",
    "guard",
    "models",
    "output",
    "client",
    "resolver",
    "options",
    "commands.health",
    "commands.whoami",
    "commands.databases",
    "commands.connect",
    "commands.schemas",
    "commands.tables",
    "commands.fields",
    "commands.query",
    "commands.cards",
    "commands.collections",
    "commands.dashboards",
    "commands.search",
    "commands.context",
    "commands.freshness",
    "commands.overview",
    "commands.batch",
    "cli",
]

FORBIDDEN = re.compile(r"from mbcli|import mbcli|from \.")


class BuildError(Exception):
    """Fatal amalgamation problem (generator or source shape changed)."""


def module_file(rel: str) -> Path:
    return SRC / Path(*rel.split(".")).with_suffix(".py")


def import_kind(node: ast.ImportFrom | ast.Import) -> str:
    """Classify an import as 'future' / 'intra' / 'external'."""
    if isinstance(node, ast.ImportFrom):
        if node.level > 0:
            return "intra"
        if node.module == "__future__":
            return "future"
        if node.module == "mbcli" or (node.module or "").startswith("mbcli."):
            return "intra"
        return "external"
    for alias in node.names:
        if alias.name == "mbcli" or alias.name.startswith("mbcli."):
            return "intra"
    return "external"


def is_main_guard(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.If) and isinstance(stmt.test, ast.Compare)):
        return False
    test = stmt.test
    return (
        isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Eq)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value == "__main__"
    )


def target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for item in target.elts:
            names.extend(target_names(item))
        return names
    raise BuildError(f"unsupported assignment target: {ast.dump(target)}")


def bound_names(stmt: ast.stmt) -> list[str]:
    """Top-level names bound by a non-import statement (for the shim)."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [stmt.name]
    if isinstance(stmt, ast.Assign):
        names: list[str] = []
        for target in stmt.targets:
            names.extend(target_names(target))
        return names
    if isinstance(stmt, ast.AnnAssign):
        if stmt.value is None:  # bare annotation binds nothing
            return []
        if isinstance(stmt.target, ast.Name):
            return [stmt.target.id]
        raise BuildError(f"unsupported annotated target: {ast.dump(stmt.target)}")
    return []  # If / Try / Expr docstrings: no direct top-level bindings captured


def collect_external(
    node: ast.ImportFrom | ast.Import,
    plains: dict[str, str],
    froms: dict[str, list[str]],
) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            key = alias.asname or alias.name.split(".")[0]
            text = f"import {alias.name}"
            if alias.asname:
                text += f" as {alias.asname}"
            plains.setdefault(key, text)
    else:
        specs = froms.setdefault(node.module or "", [])
        for alias in node.names:
            spec = alias.name + (f" as {alias.asname}" if alias.asname else "")
            if spec not in specs:
                specs.append(spec)


def collapse_blanks(lines: list[str]) -> list[str]:
    out: list[str] = []
    run = 0
    for line in lines:
        if not line.strip():
            run += 1
            if run <= 2:
                out.append("")
        else:
            run = 0
            out.append(line)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def render_shim(rel: str, names: list[str], renames: dict[str, str]) -> list[str]:
    var = rel.rsplit(".", 1)[-1]
    if not names:
        return [f"{var} = _ns()"]
    lines = [f"{var} = _ns("]
    lines += [f"    {n}={renames.get(n, n)}," for n in sorted(names)]
    lines.append(")")
    return lines


def private_renames(rel: str, names: list[str]) -> dict[str, str]:
    """Module-private top-level names get a per-module prefix in the amalgam.

    ``_finalize`` in commands.overview and output would otherwise silently
    shadow each other in the flat namespace (merge-order last-wins)."""
    leaf = rel.rsplit(".", 1)[-1]
    return {
        n: f"_{leaf}__{n}"
        for n in names
        if n.startswith("_") and not n.startswith("__") and len(n) > 1
    }


def apply_renames(lines: list[str], tree: ast.Module, renames: dict[str, str]) -> None:
    """Rewrite identifier occurrences in place, spans derived from the AST."""
    if not renames:
        return
    spans: list[tuple[int, int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in renames:
            spans.append((node.lineno - 1, node.col_offset, node.end_col_offset, renames[node.id]))
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name in renames
        ):
            line = lines[node.lineno - 1]
            start = line.index(node.name, node.col_offset)
            spans.append((node.lineno - 1, start, start + len(node.name), renames[node.name]))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                if name in renames:
                    line = lines[node.lineno - 1]
                    col = line.index(name, node.col_offset)
                    spans.append((node.lineno - 1, col, col + len(name), renames[name]))
    for li, cs, ce, new in sorted(spans, reverse=True):
        line = lines[li]
        lines[li] = line[:cs] + new + line[ce:]


def rewrite_deferred_intra(
    node: ast.AST,
    lines: list[str],
    drop: list[bool],
    replace: dict[int, str],
    deferred: list[str],
) -> bool:
    """Rewrite a nested single-line ``from mbcli.X import a [as b]`` in place.

    Returns False for unsupported shapes so the caller keeps hard-failing.
    ``a`` (the merged top-level name) is recorded in ``deferred``; an asname
    binding is emitted so the function body keeps working unchanged."""
    if not isinstance(node, ast.ImportFrom) or node.end_lineno != node.lineno:
        return False
    if any(alias.name == "*" or not alias.name.isidentifier() for alias in node.names):
        return False
    binds = [f"{alias.asname} = {alias.name}" for alias in node.names if alias.asname]
    deferred.extend(alias.name for alias in node.names)
    idx = node.lineno - 1
    raw = lines[idx]
    indent = raw[: len(raw) - len(raw.lstrip())]
    if binds:
        replace[idx] = indent + "; ".join(binds) + "  # merged intra alias"
    else:
        drop[idx] = True
    return True


def transform(
    rel: str,
    source: str,
    tree: ast.Module,
    plains: dict[str, str],
    froms: dict[str, list[str]],
    deferred: list[str],
) -> list[str]:
    """Return the merged section (banner + code + shim) for one module."""
    lines = source.splitlines()
    drop = [False] * len(lines)
    replace: dict[int, str] = {}

    def mark(node: ast.AST) -> None:
        assert isinstance(node, (ast.Import, ast.ImportFrom, ast.stmt))
        for idx in range(node.lineno - 1, node.end_lineno or node.lineno):
            if drop[idx]:
                raise BuildError(f"{rel}: overlapping removal around line {idx + 1}")
            drop[idx] = True

    body = tree.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        mark(body[0])  # module docstring

    shim_names: list[str] = []
    handled: list[ast.ImportFrom | ast.Import] = []
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            if stmt.col_offset != 0:
                raise BuildError(f"{rel}: indented top-level import at line {stmt.lineno}")
            kind = import_kind(stmt)
            if kind == "external":
                collect_external(stmt, plains, froms)
            handled.append(stmt)
            mark(stmt)  # future/intra dropped; external hoisted then dropped
            continue
        if is_main_guard(stmt):
            mark(stmt)
            continue
        shim_names.extend(bound_names(stmt))

    # Nested imports (inside try/if/def) may only ever be external and stay
    # in place; a nested intra-package or future import would break the flat
    # namespace, so refuse loudly instead of producing a broken amalgam —
    # except a deferred single-line `from mbcli.X import name [as alias]`
    # (runs at call time, when the merged top level is fully bound): rewrite
    # it to `alias = name` and record `name` for the post-merge check.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in handled:
            kind = import_kind(node)
            if kind != "external" and not rewrite_deferred_intra(
                node, lines, drop, replace, deferred
            ):
                raise BuildError(
                    f"{rel}: unsupported nested {kind} import"
                    f" at line {node.lineno}; extend build-single.py first"
                )

    renames = private_renames(rel, shim_names)
    apply_renames(lines, tree, renames)
    kept = collapse_blanks(
        [replace.get(idx, line) for idx, line in enumerate(lines) if not drop[idx]]
    )
    if not kept:
        raise BuildError(f"{rel}: module contributed no code")
    relpath = module_file(rel).relative_to(REPO).as_posix()
    banner = [
        "#" + "-" * 75,
        f"# mbcli.{rel}  (merged: {relpath})",
        "#" + "-" * 75,
    ]
    return banner + kept + [""] + render_shim(rel, shim_names, renames)


def read_version() -> str:
    """Extract __version__ from src/mbcli/__init__.py (shape-checked)."""
    path = SRC / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    version: str | None = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue  # docstring
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            continue
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "__version__"
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            version = stmt.value.value
            continue
        raise BuildError(
            "src/mbcli/__init__.py gained top-level code beyond __version__;"
            " extend build-single.py to handle it"
        )
    if version is None:
        raise BuildError("__version__ missing from src/mbcli/__init__.py")
    return version


def check_commands_init() -> None:
    """commands/__init__.py must stay docstring-only (contributes no code)."""
    path = SRC / "commands" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            continue
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__":
            continue
        raise BuildError(
            "src/mbcli/commands/__init__.py gained top-level code;"
            " extend build-single.py to handle it"
        )


def build_stamp() -> str:
    sde = os.environ.get("SOURCE_DATE_EPOCH")
    if sde:
        ts = int(sde)
    else:  # newest source mtime: real, yet stable across back-to-back runs
        ts = max(int(module_file(rel).stat().st_mtime) for rel in MODULES)
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_imports(plains: dict[str, str], froms: dict[str, list[str]]) -> list[str]:
    lines = ["import types"]  # generator-added: namespace shims (_ns)
    for key, stmt in plains.items():
        if key != "types":
            lines.append(stmt)
    if froms:
        lines.append("")
        for module, specs in froms.items():
            lines.append(f"from {module} import {', '.join(sorted(specs))}")
    return lines


def assemble(
    version: str,
    stamp: str,
    plains: dict[str, str],
    froms: dict[str, list[str]],
    sections: list[list[str]],
) -> list[str]:
    head = [
        "#!/usr/bin/env python3",
        '"""mbcli 单文件脚本 —— 由 scripts/build-single.py 自动生成，请勿手改。',
        "",
        "源码 src/mbcli/ 为权威实现（测试与开发都在源码上进行）。",
        "",
        "目标机要求：Python >= 3.10，且已安装 typer>=0.12 与 httpx>=0.27",
        "（rich 由 typer 携带，仅 --format table 需要；依赖不随本文件打包，",
        "直接使用目标机自带的库）。",
        "",
        "用法：python3 mbcli.py …（与安装版 mbcli 命令完全等价）。",
        "",
        "重新生成：uv run python scripts/build-single.py（修改 src/ 后必须重新生成；需在装有 typer/httpx 的环境运行）。",
        "",
        f"生成时间：{stamp}（SOURCE_DATE_EPOCH 可覆盖，便于可复现构建）",
        f"mbcli 版本：{version}",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    meta = ["", f'__version__ = "{version}"', "", "_ns = types.SimpleNamespace"]
    tail = ["", "", 'if __name__ == "__main__":', "    main()"]
    out = head + render_imports(plains, froms) + meta
    for section in sections:
        out += ["", ""]
        out += section
    return out + tail


def detect_public_dup_hazards(parsed: list[tuple[str, str, ast.Module]]) -> None:
    """Fail on differing duplicate top-level names that are unsafe to flatten.

    A name defined in several modules resolves last-wins in the amalgam.
    Attribute access is shim-protected and top-level own-section references
    execute before later rebinding, so the only hazard is a bare reference
    inside a function body of a module that is not the last definer."""
    defs: dict[str, list[str]] = {}
    segs: dict[str, set[str]] = {}
    for rel, source, tree in parsed:
        for stmt in tree.body:
            names: list[str] = []
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names = [stmt.name]
            elif isinstance(stmt, ast.Assign):
                names = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
            for name in names:
                if name.startswith("_"):
                    continue  # private names are defused by private_renames()
                defs.setdefault(name, []).append(rel)
                segs.setdefault(name, set()).add(ast.get_source_segment(source, stmt) or "")
    for name, modlist in defs.items():
        if len(modlist) < 2 or len(segs[name]) < 2:
            continue
        last = modlist[-1]
        for rel, _source, tree in parsed:
            if rel == last:
                continue
            for stmt in tree.body:
                # Only function bodies resolve names at call time (last-wins).
                # Decorators and defaults evaluate at def time within the
                # module's own section — before later rebinding — so walk only
                # the body statements, not the FunctionDef node itself.
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for child in stmt.body:
                    for node in ast.walk(child):
                        if (
                            isinstance(node, ast.Name)
                            and node.id == name
                            and isinstance(node.ctx, ast.Load)
                        ):
                            raise BuildError(
                                f"public name {name!r} differs across {modlist}; bare reference "
                                f"in {rel} would resolve to the last definition ({last}) — "
                                f"rename it or access it via the module attribute"
                            )


def build(deferred: list[str]) -> str:
    plains: dict[str, str] = {}
    froms: dict[str, list[str]] = {}
    sections: list[list[str]] = []
    parsed: list[tuple[str, str, ast.Module]] = []
    for rel in MODULES:
        path = module_file(rel)
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        parsed.append((rel, source, tree))
        sections.append(transform(rel, source, tree, plains, froms, deferred))
    detect_public_dup_hazards(parsed)
    return "\n".join(assemble(read_version(), build_stamp(), plains, froms, sections)) + "\n"


def _top_level_names(tree: ast.Module) -> set[str]:
    """All names bound at the merged module's top level (for deferred checks)."""
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    names.update(e.id for e in target.elts if isinstance(e, ast.Name))
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            names.add(stmt.target.id)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def self_checks(text: str, deferred: list[str]) -> None:
    tree = ast.parse(text, filename=str(OUT))  # 1. parses
    hit = FORBIDDEN.search(text)  # 2. no intra-package references survived
    if hit is not None:
        raise BuildError(f"intra-package reference survived the transform: {hit.group()!r}")
    missing = sorted({n for n in deferred if n not in _top_level_names(tree)})
    if missing:  # 2b. deferred intra imports resolve at run time
        raise BuildError(f"deferred intra imports unresolvable in merged file: {missing}")
    try:  # 3. runs: registration code executes at import time
        proc = subprocess.run(
            [sys.executable, str(OUT), "--help"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError("--help self-check timed out") from exc
    if proc.returncode != 0:
        raise BuildError(f"--help self-check failed ({proc.returncode}):\n{proc.stderr}")


def main() -> int:
    try:
        check_commands_init()
        deferred: list[str] = []
        text = build(deferred)
        OUT.write_text(text, encoding="utf-8")
        self_checks(text, deferred)
    except (BuildError, OSError, SyntaxError, ValueError) as exc:
        print(f"build-single: FAIL: {exc}", file=sys.stderr)
        return 1
    lines = text.count("\n")
    print(f"build-single: wrote {OUT.name}: {len(text.encode('utf-8'))} bytes, {lines} lines")
    print(f"build-single: merged {len(MODULES)} modules; self-checks passed (parse/refs/--help)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
