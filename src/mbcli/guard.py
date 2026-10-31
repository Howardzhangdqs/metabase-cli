"""Write guard (DESIGN §3.4): a lexer-aware, defense-in-depth heuristic.

Not a security boundary (the DB account's read-only grants are); the guard
strips comments with string-literal awareness, splits statements on ';' outside
literals, and classifies EVERY statement's first keyword (Round-1 eval P1-1):

- in READONLY_KEYWORDS (SELECT / WITH / SHOW / DESCRIBE / EXPLAIN) → pass;
- in WRITE_KEYWORDS (a known mutating/admin statement starter: DELETE, DROP,
  SET, …) → write_blocked (exit 2, --allow-write escape hatch);
- anything else — a typo like SELECTNOSUCH/SEL/se1ect, a clause word like
  FROM, non-SQL text — → invalid_args (exit 2). These statements can never
  become runnable via --allow-write, so the error must NOT advertise it.
"""

from __future__ import annotations

import re

from mbcli.errors import MbcliError

READONLY_KEYWORDS = frozenset({"SELECT", "WITH", "SHOW", "DESCRIBE", "EXPLAIN"})

# Statement-starting keywords that mutate state or manage transactions /
# sessions / admin operations. Rejected as write_blocked with the --allow-write
# hint. MySQL statement starters that are neither read-only nor in this set
# fall through to invalid_args ("unrecognized leading keyword").
WRITE_KEYWORDS = frozenset(
    {
        "ALTER",
        "ANALYZE",
        "BEGIN",
        "BINLOG",
        "CACHE",
        "CALL",
        "CHANGE",
        "CHECK",
        "CLONE",
        "COMMIT",
        "CREATE",
        "DEALLOCATE",
        "DELETE",
        "DISABLE",
        "DO",
        "DROP",
        "ENABLE",
        "EXECUTE",
        "FLUSH",
        "GRANT",
        "HANDLER",
        "IMPORT",
        "INSERT",
        "INSTALL",
        "KILL",
        "LOAD",
        "LOCK",
        "MERGE",
        "OPTIMIZE",
        "PREPARE",
        "PURGE",
        "RENAME",
        "REPAIR",
        "REPLACE",
        "RESET",
        "RESIGNAL",
        "REVOKE",
        "ROLLBACK",
        "SAVEPOINT",
        "SET",
        "SHUTDOWN",
        "SIGNAL",
        "START",
        "STOP",
        "TRUNCATE",
        "UNINSTALL",
        "UNLOCK",
        "UPDATE",
        "USE",
        "XA",
    }
)

_LEADING_KEYWORD = re.compile(r"[\s(]*([A-Za-z_][A-Za-z_0-9]*)")


def scan_statements(sql: str) -> list[str]:
    """Strip comments (string-aware) and split on ';' outside literals/backticks."""
    statements: list[str] = []
    current: list[str] = []
    i = 0
    n = len(sql)
    quote: str | None = None  # "'", '"', or '`' while inside a literal
    while i < n:
        ch = sql[i]
        if quote is not None:
            current.append(ch)
            if ch == "\\" and quote in ("'", '"') and i + 1 < n:
                current.append(sql[i + 1])
                i += 2
                continue
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:  # doubled-quote escape
                    current.append(sql[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":  # -- line comment
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "#":  # MySQL line comment
            while i < n and sql[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":  # block comment
            i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                i += 1
            i = min(i + 2, n)
            continue
        if ch == ";":
            statements.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    statements.append("".join(current))
    return [statement.strip() for statement in statements if statement.strip()]


def first_keyword(statement: str) -> str | None:
    match = _LEADING_KEYWORD.match(statement)
    return match.group(1).upper() if match else None


def check_readonly(sql: str, allow_write: bool = False) -> list[str]:
    """Validate that every statement is read-only. Raises MbcliError otherwise.

    Returns the list of (comment-stripped) statements. With allow_write=True the
    check is bypassed entirely and an empty list is returned.
    """
    if allow_write:
        return []
    statements = scan_statements(sql)
    if not statements:
        raise MbcliError(
            "invalid_args",
            "SQL contains no statements (empty or comments only)",
        )
    for statement in statements:
        keyword = first_keyword(statement)
        if keyword is None:
            raise MbcliError(
                "invalid_args",
                "语句未以 SQL 关键字开头（疑似粘贴/拼写错误；只读白名单:"
                " SELECT/WITH/SHOW/DESCRIBE/EXPLAIN）",
                detail={"statement": statement[:120]},
            )
        if keyword not in READONLY_KEYWORDS:
            if keyword in WRITE_KEYWORDS:
                raise MbcliError(
                    "write_blocked",
                    f"non-read-only statement rejected (leading keyword {keyword});"
                    " re-run with --allow-write to allow write operations",
                    detail={"keyword": keyword, "statement": statement[:120]},
                )
            # Round-1 eval P1-1: a leading token that is not any known SQL
            # statement keyword (SELECTNOSUCH, SEL, se1ect, FROM, …) is a
            # usage error — --allow-write cannot make it runnable, so the
            # message must never suggest it.
            raise MbcliError(
                "invalid_args",
                f"无法识别的首关键词 `{keyword}`（疑似拼写错误；只读白名单:"
                " SELECT/WITH/SHOW/DESCRIBE/EXPLAIN）",
                detail={"keyword": keyword, "statement": statement[:120]},
            )
    return statements
