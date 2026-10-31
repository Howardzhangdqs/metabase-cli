"""Output layer (DESIGN §3.6, §5.2): json/csv/ndjson/table, --fields, --out, truncation."""

from __future__ import annotations

import json

import pytest

from mbcli.errors import MbcliError
from mbcli.models import DatabaseInfo, QueryEnvelope
from mbcli.output import (
    Format,
    emit_envelope,
    emit_object,
    emit_records,
    prune_fields,
    render_csv,
    render_json,
)

RECORDS = [
    {"id": 7, "name": "bi_data", "engine": "mysql"},
    {"id": 15, "name": "quant_data", "engine": "mysql"},
]


# -- json -----------------------------------------------------------------------


def test_json_key_order_matches_dataclass_declaration():
    info = DatabaseInfo(
        id=7,
        name="bi_data",
        engine="mysql",
        description=None,
        is_sample=False,
        initial_sync_status="complete",
        timezone="UTC",
        dbms_version="8.0.33",
    )
    assert list(info.to_dict()) == [
        "id",
        "name",
        "engine",
        "description",
        "is_sample",
        "initial_sync_status",
        "timezone",
        "dbms_version",
    ]
    rendered = render_json(info)
    assert rendered.startswith('{"id": 7, "name": "bi_data", "engine": "mysql"')
    assert list(json.loads(rendered)) == list(info.to_dict())


def test_json_ensure_ascii_false():
    rendered = render_json({"name": "数据日期", "emoji": "✅"})
    assert "数据日期" in rendered
    assert "✅" in rendered
    assert "\\u" not in rendered


def test_emit_records_json(capsys):
    emit_records([dict(r) for r in RECORDS])
    assert capsys.readouterr().out == json.dumps(RECORDS, ensure_ascii=False) + "\n"


def test_emit_object_json(capsys):
    emit_object({"status": "ok", "version": "v0.66"})
    assert capsys.readouterr().out == '{"status": "ok", "version": "v0.66"}\n'


# -- csv (RFC 4180) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_cell"),
    [
        ("plain", "plain"),
        ("comma,inside", '"comma,inside"'),
        ('say "hi"', '"say ""hi"""'),
        ("line1\nline2", '"line1\nline2"'),
        ("carriage\rreturn", '"carriage\rreturn"'),
        ("数据日期", "数据日期"),
        (None, '""'),  # a lone empty field is quoted so the line is not blank
        (42, "42"),
    ],
)
def test_csv_escaping(value, expected_cell):
    assert render_csv(["col"], [[value]]) == f"col\r\n{expected_cell}\r\n"


def test_csv_header_and_multiple_rows():
    out = render_csv(["a", "b"], [[1, "x"], [2, "y"]])
    assert out == "a,b\r\n1,x\r\n2,y\r\n"


def test_emit_records_csv(capsys):
    emit_records([dict(r) for r in RECORDS], fmt=Format.csv)
    assert capsys.readouterr().out == "id,name,engine\r\n7,bi_data,mysql\r\n15,quant_data,mysql\r\n"


def test_emit_records_csv_fields_select_columns(capsys):
    emit_records([dict(r) for r in RECORDS], fmt=Format.csv, fields="name,id")
    assert capsys.readouterr().out == "name,id\r\nbi_data,7\r\nquant_data,15\r\n"


# -- ndjson -------------------------------------------------------------------------


def envelope_dict(max_rows: int = 0, rows: int = 3) -> dict:
    payload = {
        "status": "completed",
        "data": {
            "cols": [
                {"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"},
                {"name": "label", "base_type": "type/Text", "database_type": "VARCHAR"},
            ],
            "rows": [[i, f"行{i}"] for i in range(rows)],
        },
        "running_time": 123,
        "cached": False,
    }
    return QueryEnvelope.from_response(payload, max_rows=max_rows).to_dict()


def test_emit_envelope_ndjson_shape(capsys):
    emit_envelope(envelope_dict(), fmt=Format.ndjson)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 + 3 + 1  # envelope + rows + sentinel
    header = json.loads(lines[0])
    assert header == {
        "columns": [
            {"name": "one", "base_type": "type/BigInteger", "database_type": "BIGINT"},
            {"name": "label", "base_type": "type/Text", "database_type": "VARCHAR"},
        ]
    }
    assert json.loads(lines[1]) == [0, "行0"]
    assert json.loads(lines[-1]) == {"truncated": False, "rows_returned": 3}


def test_emit_envelope_ndjson_truncated_sentinel(capsys):
    emit_envelope(envelope_dict(max_rows=2), fmt=Format.ndjson)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1 + 2 + 1
    assert json.loads(lines[-1]) == {"truncated": True, "rows_returned": 2}


def test_emit_envelope_csv(capsys):
    emit_envelope(envelope_dict(max_rows=1), fmt=Format.csv)
    assert capsys.readouterr().out == "one,label\r\n0,行0\r\n"


def test_emit_envelope_json(capsys):
    emit_envelope(envelope_dict(), fmt=Format.json)
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "completed"
    assert parsed["truncated"] is False
    assert parsed["rows_returned"] == 3
    assert list(parsed) == [
        "status",
        "row_count",
        "rows_returned",
        "truncated",
        "columns",
        "rows",
        "running_time_ms",
        "cached",
        "data_warnings",
    ]


# -- --fields --------------------------------------------------------------------------


def test_fields_prune_record_keys_only(capsys):
    emit_records([dict(r) for r in RECORDS], fields="id,name")
    out = capsys.readouterr().out
    assert json.loads(out) == [{"id": 7, "name": "bi_data"}, {"id": 15, "name": "quant_data"}]


def test_fields_order_follows_option_not_record(capsys):
    emit_records([dict(r) for r in RECORDS], fields="name,id")
    assert json.loads(capsys.readouterr().out)[0] == {"name": "bi_data", "id": 7}


def test_fields_misspelled_is_invalid_args():
    with pytest.raises(MbcliError) as excinfo:
        prune_fields(RECORDS, ["id", "nmae"])
    assert excinfo.value.code == "invalid_args"
    assert excinfo.value.exit_code == 2
    assert excinfo.value.detail["unknown"] == ["nmae"]
    assert "name" in excinfo.value.detail["available"]


def test_fields_on_envelope_is_rejected():
    with pytest.raises(MbcliError) as excinfo:
        emit_envelope(envelope_dict(), fields="id")
    assert excinfo.value.code == "invalid_args"


def test_fields_does_not_prune_envelope_keys(capsys):
    """The envelope keys must survive even though a prune-like option was used elsewhere."""
    emit_envelope(envelope_dict(), fmt=Format.json)
    parsed = json.loads(capsys.readouterr().out)
    assert set(parsed) >= {"status", "row_count", "rows_returned", "truncated", "columns", "rows"}


def test_fields_whitespace_and_empty_entries(capsys):
    emit_records([dict(r) for r in RECORDS], fields=" id , name ")
    assert json.loads(capsys.readouterr().out)[0] == {"id": 7, "name": "bi_data"}


# -- truncation (§5.2, ADR #12) ----------------------------------------------------------


def make_query_payload(rows: int, rows_truncated: bool = False) -> dict:
    return {
        "status": "completed",
        "data": {
            "cols": [{"name": "n", "base_type": "type/Integer", "database_type": "INT"}],
            "rows": [[i] for i in range(rows)],
            "rows_truncated": rows_truncated,
        },
        "running_time": 5,
        "cached": False,
    }


@pytest.mark.parametrize(
    ("total", "max_rows", "expected_returned", "expected_truncated"),
    [
        (5, 2, 2, True),
        (5, 5, 5, False),
        (5, 10, 5, False),
        (5, 0, 5, False),  # 0 = unlimited
        (0, 100, 0, False),
    ],
)
def test_max_rows_truncation_semantics(total, max_rows, expected_returned, expected_truncated):
    envelope = QueryEnvelope.from_response(make_query_payload(total), max_rows=max_rows)
    assert envelope.rows_returned == expected_returned
    assert envelope.truncated is expected_truncated
    assert len(envelope.rows) == expected_returned
    assert envelope.row_count == total  # server-side count of this response


def test_server_side_truncation_becomes_data_warning():
    envelope = QueryEnvelope.from_response(make_query_payload(3, rows_truncated=True))
    assert envelope.data_warnings == [
        "server truncated rows in this response (data.rows_truncated)"
    ]


def test_envelope_defaults():
    envelope = QueryEnvelope.from_response({"status": "completed", "data": {}})
    assert envelope.rows == []
    assert envelope.columns == []
    assert envelope.data_warnings == []
    assert envelope.cached is False


# -- --out ---------------------------------------------------------------------------


def test_out_writes_file_and_stdout_summary(capsys, tmp_path):
    target = tmp_path / "out.json"
    emit_records([dict(r) for r in RECORDS], out=str(target))
    summary = json.loads(capsys.readouterr().out)
    assert list(summary) == ["out", "rows", "truncated"]
    assert summary == {"out": str(target), "rows": 2, "truncated": False}
    assert json.loads(target.read_text()) == RECORDS


def test_out_with_envelope_summary_uses_rows_returned(capsys, tmp_path):
    target = tmp_path / "out.ndjson"
    emit_envelope(envelope_dict(max_rows=2), fmt=Format.ndjson, out=str(target))
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"out": str(target), "rows": 2, "truncated": True}
    assert target.read_text().endswith('{"truncated": true, "rows_returned": 2}\n')


def test_out_csv_content_preserved_exactly(capsys, tmp_path):
    target = tmp_path / "out.csv"
    emit_records([dict(r) for r in RECORDS], fmt=Format.csv, out=str(target))
    capsys.readouterr()
    assert target.read_bytes() == b"id,name,engine\r\n7,bi_data,mysql\r\n15,quant_data,mysql\r\n"


# -- table format ---------------------------------------------------------------------


def test_emit_records_table_shows_headers_and_rows(capsys):
    emit_records([dict(r) for r in RECORDS], fmt=Format.table)
    out = capsys.readouterr().out
    assert "id" in out and "name" in out and "engine" in out
    assert "bi_data" in out and "quant_data" in out


def test_emit_object_table(capsys):
    emit_object({"status": "ok", "version": "v1"}, fmt=Format.table)
    out = capsys.readouterr().out
    assert "status" in out and "ok" in out


def test_emit_object_rejects_csv_and_ndjson():
    for fmt in (Format.csv, Format.ndjson):
        with pytest.raises(MbcliError) as excinfo:
            emit_object({"a": 1}, fmt=fmt)
        assert excinfo.value.code == "invalid_args"
