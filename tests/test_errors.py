"""Error code table (DESIGN §3.3) and the stderr JSON contract."""

from __future__ import annotations

import json

import pytest

from mbcli.errors import ERROR_EXIT_CODES, MbcliError


@pytest.mark.parametrize(
    ("code", "expected_exit"),
    [
        # the full §3.3 normalization table
        ("auth_failed", 1),
        ("permission_denied", 1),
        ("not_found", 1),
        ("bad_query", 1),
        ("invalid_request", 1),
        ("rate_limited", 1),
        ("server_error", 1),
        ("network_error", 1),
        ("timeout", 1),
        ("config_missing", 1),
        ("unhealthy", 1),
        ("ambiguous_ref", 1),
        ("invalid_response", 1),
        ("invalid_args", 2),
        ("write_blocked", 2),
        ("not_implemented", 1),
    ],
)
def test_exit_codes(code: str, expected_exit: int) -> None:
    assert MbcliError(code, "boom").exit_code == expected_exit
    assert ERROR_EXIT_CODES[code] == expected_exit


def test_unknown_code_defaults_to_exit_1() -> None:
    assert MbcliError("something_new", "boom").exit_code == 1


def test_raised_and_caught_as_exception() -> None:
    with pytest.raises(MbcliError) as excinfo:
        raise MbcliError("bad_query", "nope", {"http_status": 400})
    assert excinfo.value.code == "bad_query"
    assert str(excinfo.value) == "nope"


def test_stderr_json_shape() -> None:
    error = MbcliError("bad_query", "Table 'quant_data.x' doesn't exist", {"http_status": 400})
    line = error.to_stderr_json()
    assert "\n" not in line
    parsed = json.loads(line)
    assert set(parsed) == {"error"}
    body = parsed["error"]
    assert body["code"] == "bad_query"
    assert body["message"] == "Table 'quant_data.x' doesn't exist"
    assert body["detail"] == {"http_status": 400}


def test_stderr_json_ensure_ascii_false() -> None:
    line = MbcliError("not_found", "库 数据日期 不存在").to_stderr_json()
    assert "库 数据日期 不存在" in line
    assert "\\u" not in line


def test_stderr_json_message_newlines_are_escaped() -> None:
    line = MbcliError("server_error", "line one\nline two").to_stderr_json()
    assert "\n" not in line
    assert json.loads(line)["error"]["message"] == "line one line two"


def test_detail_defaults_to_empty_object() -> None:
    parsed = json.loads(MbcliError("timeout", "slow").to_stderr_json())
    assert parsed["error"]["detail"] == {}


def test_emit_writes_single_line_to_stream() -> None:
    import io

    stream = io.StringIO()
    MbcliError("rate_limited", "slow down").emit(stream)
    output = stream.getvalue()
    assert output.count("\n") == 1
    assert json.loads(output)["error"]["code"] == "rate_limited"
