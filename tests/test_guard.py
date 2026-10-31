"""Write guard matrix (DESIGN §3.4): lexer-aware comment stripping, splitting,
keyword whitelist, --allow-write bypass."""

from __future__ import annotations

import pytest

from mbcli.errors import MbcliError
from mbcli.guard import check_readonly, first_keyword, scan_statements

ALLOWED = [
    # plain read-only statements
    "SELECT 1",
    "select * from t where x = 1",
    "WITH cte AS (SELECT 1 AS n) SELECT * FROM cte",
    "SHOW TABLES",
    "describe app_mau",
    "EXPLAIN SELECT 1",
    # multiple read-only statements
    "SELECT 1; SELECT 2",
    "SELECT 1 ; SELECT 2",
    # comments
    "/* header */ SELECT 1",
    "SELECT 1 -- trailing comment",
    "SELECT 1 # mysql comment",
    "SELECT 1 /* mid */ + 2",
    "SELECT 'a' /* c */ , 'b'",
    "-- leading\nSELECT 1",
    "/* c1 */ SELECT 1; /* c2 */ SELECT 2",
    # keywords inside literals must be ignored
    "SELECT '-- not a comment' FROM t",
    "SELECT 'a;b' FROM t",
    "SELECT '# hash' FROM t",
    "SELECT 'DELETE FROM t' AS danger",
    "SELECT `delete` FROM `drop-table`",
    'SELECT "delete" FROM "weird table"',
    "SELECT 'it''s quoted' FROM t",
    "SELECT 'back\\\\slash' FROM t",
    "WITH q AS (SELECT 'DELETE FROM t' AS x) SELECT * FROM q",
    # semicolon inside a string does not split
    "SELECT 'a;DROP TABLE t'; SELECT 2",
    # parenthesized selects
    "(SELECT 1)",
    "( SELECT 1 ) UNION ( SELECT 2 )",
]

BLOCKED = [
    # single write statements
    "DELETE FROM t",
    "UPDATE t SET x = 1",
    "INSERT INTO t VALUES (1)",
    "DROP TABLE t",
    "TRUNCATE TABLE t",
    "ALTER TABLE t ADD c INT",
    "CREATE TABLE t (x INT)",
    "GRANT ALL ON *.* TO u",
    "REPLACE INTO t VALUES (1)",
    "rename TABLE t TO t2",
    # mixed multi-statement: EVERY statement must be read-only
    "SELECT 1; DELETE FROM t",
    "SELECT 1; SELECT 2; DROP TABLE t",
    "select 1;drop table t",
    # comment before keyword must not hide it
    "/* c */ DELETE FROM t",
    "DELETE /* c */ FROM t",
    "-- comment\nDELETE FROM t",
    "# comment\nUPDATE t SET x = 1",
    "/* outer /* nested */ DELETE FROM t",
    # statement without a leading keyword
    "123",
    "FROM t SELECT x",
    "??",
]


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql: str) -> None:
    assert check_readonly(sql)  # returns the non-empty statement list


@pytest.mark.parametrize("sql", BLOCKED)
def test_blocked(sql: str) -> None:
    with pytest.raises(MbcliError) as excinfo:
        check_readonly(sql)
    assert excinfo.value.code == "write_blocked"
    assert excinfo.value.exit_code == 2
    assert "--allow-write" in excinfo.value.message


@pytest.mark.parametrize(
    "sql", ["", "   ", "\n\t", "-- only a comment", "# only too", "/* block */"]
)
def test_empty_or_comment_only_is_invalid_args(sql: str) -> None:
    with pytest.raises(MbcliError) as excinfo:
        check_readonly(sql)
    assert excinfo.value.code == "invalid_args"
    assert excinfo.value.exit_code == 2


@pytest.mark.parametrize(
    "sql", ["DELETE FROM t", "DROP TABLE t; DELETE FROM t", "INSERT INTO t VALUES (1)"]
)
def test_allow_write_bypasses_everything(sql: str) -> None:
    assert check_readonly(sql, allow_write=True) == []


# -- lexer details ---------------------------------------------------------------


def test_scan_strips_comments_but_keeps_statements() -> None:
    assert scan_statements("SELECT 1 -- c\n; SELECT 2") == ["SELECT 1", "SELECT 2"]
    assert scan_statements("SELECT 1;; ;SELECT 2") == ["SELECT 1", "SELECT 2"]
    assert scan_statements("/* only comment */") == []
    assert scan_statements("") == []


def test_scan_keeps_escaped_quotes_intact() -> None:
    assert scan_statements("SELECT 'it''s'; SELECT 2") == ["SELECT 'it''s'", "SELECT 2"]


def test_semicolon_inside_backticks_does_not_split() -> None:
    assert scan_statements("SELECT `a;b` FROM t") == ["SELECT `a;b` FROM t"]


def test_double_dash_inside_string_is_literal() -> None:
    # 'a--b' is a literal, so the statement survives intact
    assert scan_statements("SELECT 'a--b'") == ["SELECT 'a--b'"]


def test_block_comment_hides_write_keyword() -> None:
    statements = scan_statements("SELECT 1 /* DELETE FROM t */")
    assert statements == ["SELECT 1"]


@pytest.mark.parametrize(
    ("statement", "keyword"),
    [
        ("SELECT 1", "SELECT"),
        ("  with x as (select 1) select * from x", "WITH"),
        ("(select 1)", "SELECT"),
        ("((SELECT 1))", "SELECT"),
        ("show tables", "SHOW"),
        ("123", None),
        ("", None),
    ],
)
def test_first_keyword(statement: str, keyword: str | None) -> None:
    assert first_keyword(statement) == keyword


def test_write_blocked_detail_includes_keyword_and_statement() -> None:
    with pytest.raises(MbcliError) as excinfo:
        check_readonly("SELECT 1; DELETE FROM big_table WHERE id = 1")
    assert excinfo.value.detail["keyword"] == "DELETE"
    assert "DELETE FROM big_table" in excinfo.value.detail["statement"]
