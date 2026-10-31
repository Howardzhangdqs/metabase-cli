"""End-to-end CLI tests: exit-code contract (0/1/2), stderr JSON, option grammar."""

from __future__ import annotations

import json

import httpx
import pytest

BASE = "http://test.local"

DATABASES_PAYLOAD = {
    "data": [
        {
            "id": 7,
            "name": "bi_data",
            "engine": "mysql",
            "description": None,
            "is_sample": False,
            "initial_sync_status": "complete",
            "timezone": "Asia/Shanghai",
            "dbms_version": "8.0.33",
            "unknown_extra": "dropped",
        },
        {
            "id": 15,
            "name": "quant_data",
            "engine": "mysql",
            "description": "量化",
            "is_sample": False,
            "initial_sync_status": "complete",
            "timezone": "Asia/Shanghai",
            "dbms_version": "8.0.33",
        },
    ],
    "total": 2,
}

CURRENT_USER = {
    "id": 42,
    "email": "agent@example.com",
    "is_superuser": False,
    "group_ids": [1, 10],
    "first_name": "A",
    "common_name": "dropped",
}


def mock_whoami(mock_api, base: str = BASE):
    route = mock_api.get(f"{base}/api/user/current")
    route.mock(return_value=httpx.Response(200, json=CURRENT_USER))
    return route


# -- help / usage errors -----------------------------------------------------------


def test_help_exit_0_and_full_command_tree(cli):
    result = cli("--help").assert_ok(0)
    for command in (
        "health",
        "whoami",
        "databases",
        "schemas",
        "tables",
        "fields",
        "query",
        "cards",
        "collections",
        "dashboards",
        "search",
        "context",
    ):
        assert command in result.stdout, f"missing {command} in --help"


def test_bare_invocation_shows_help(cli):
    result = cli().assert_ok(0)
    assert "Usage" in result.stdout


def test_subcommand_help(cli):
    cli("databases", "--help").assert_ok(0)
    cli("query", "--help").assert_ok(0)


@pytest.mark.parametrize(
    "argv",
    [
        ("--bogus-flag", "health"),
        ("health", "--bogus-flag"),
        ("frobnicate", "--url", BASE),
        ("--url", BASE, "whoami", "--api-key", "k"),  # root option AFTER subcommand
        ("--timeout", "notanint", "health"),
    ],
)
def test_usage_errors_exit_2_with_invalid_args_json(cli, argv):
    result = cli(*argv)
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert error["message"]


# -- option grammar (§3.7) -------------------------------------------------------------


def test_root_options_before_subcommand_work(cli, mock_api):
    route = mock_whoami(mock_api)
    cli("--url", BASE, "--api-key", "key", "whoami").assert_ok(0)
    assert route.calls.last.request.headers["X-API-Key"] == "key"


def test_root_options_via_environment(cli, mock_api, monkeypatch):
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_API_KEY", "key2")
    route = mock_whoami(mock_api)
    cli("whoami").assert_ok(0)
    assert route.calls.last.request.headers["X-API-Key"] == "key2"


def test_no_color_flag_accepted(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    cli(
        "--url", BASE, "--api-key", "k", "--no-color", "databases", "list", "--format", "table"
    ).assert_ok(0)


# -- health ------------------------------------------------------------------------


def test_health_ok(cli, mock_api):
    mock_api.get(f"{BASE}/api/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    mock_api.get(f"{BASE}/api/session/properties").mock(
        return_value=httpx.Response(200, json={"version": {"tag": "v0.66.0-SNAPSHOT"}})
    )
    result = cli("--url", BASE, "health").assert_ok(0)
    assert result.json() == {"status": "ok", "version": "v0.66.0-SNAPSHOT"}


def test_health_no_credentials_needed(cli, mock_api):
    """health must not attempt login even with zero credentials configured."""
    login = mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "should-not-happen"})
    )
    mock_api.get(f"{BASE}/api/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    mock_api.get(f"{BASE}/api/session/properties").mock(return_value=httpx.Response(200, json={}))
    cli("--url", BASE, "health").assert_ok(0)
    assert login.call_count == 0


def test_health_initializing_is_unhealthy_exit_1(cli, mock_api):
    mock_api.get(f"{BASE}/api/health").mock(
        return_value=httpx.Response(503, json={"status": "initializing", "progress": 30})
    )
    mock_api.get(f"{BASE}/api/session/properties").mock(return_value=httpx.Response(200, json={}))
    result = cli("--url", BASE, "health")
    assert result.exit_code == 1
    error = result.error()
    assert error["code"] == "unhealthy"
    assert error["detail"]["body"] == {"status": "initializing", "progress": 30}


# -- whoami ------------------------------------------------------------------------


def test_whoami_output_contract(cli, mock_api):
    mock_whoami(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "whoami").assert_ok(0)
    assert result.json() == {
        "id": 42,
        "email": "agent@example.com",
        "is_superuser": False,
        "group_ids": [1, 10],
    }
    assert list(result.json()) == ["id", "email", "is_superuser", "group_ids"]


def test_whoami_missing_url_is_config_missing(cli):
    result = cli("whoami")
    assert result.exit_code == 1
    assert result.error()["code"] == "config_missing"


def test_whoami_lazy_credentials(cli, mock_api):
    """No api key and no username/password: fails only at the first auth attempt."""
    mock_whoami(mock_api)
    result = cli("--url", BASE, "whoami")
    assert result.exit_code == 1
    assert result.error()["code"] == "config_missing"


def test_whoami_session_login_end_to_end(cli, mock_api, tmp_path, monkeypatch):
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "user@example.com")
    monkeypatch.setenv("MB_PASSWORD", "pw12345")
    cache = tmp_path / "session.json"
    cache.write_text(json.dumps({"session_id": "cached", "obtained_at": 99999999999}))
    login = mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "fresh"})
    )
    route = mock_api.get(f"{BASE}/api/user/current").mock(
        side_effect=[
            httpx.Response(401, text="Unauthenticated"),
            httpx.Response(200, json=CURRENT_USER),
        ]
    )
    result = cli("whoami")
    result.assert_ok(0)
    assert result.json()["id"] == 42
    assert route.calls[0].request.headers["X-Metabase-Session"] == "cached"
    assert route.calls[1].request.headers["X-Metabase-Session"] == "fresh"
    assert login.call_count == 1


# -- databases list ------------------------------------------------------------------


def test_databases_list_json_contract(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    result = cli("--url", BASE, "--api-key", "k", "databases", "list").assert_ok(0)
    parsed = result.json()
    assert isinstance(parsed, list) and len(parsed) == 2
    assert list(parsed[0]) == [
        "id",
        "name",
        "engine",
        "description",
        "is_sample",
        "initial_sync_status",
        "timezone",
        "dbms_version",
    ]
    assert parsed[1]["description"] == "量化"
    assert "unknown_extra" not in parsed[0]


def test_databases_list_fields(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    result = cli(
        "--url", BASE, "--api-key", "k", "databases", "list", "--fields", "id,name"
    ).assert_ok(0)
    assert result.json() == [
        {"id": 7, "name": "bi_data"},
        {"id": 15, "name": "quant_data"},
    ]


def test_databases_list_bad_fields_exit_2(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    result = cli("--url", BASE, "--api-key", "k", "databases", "list", "--fields", "id,oops")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_databases_list_csv(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    result = cli("--url", BASE, "--api-key", "k", "databases", "list", "--format", "csv").assert_ok(
        0
    )
    assert result.stdout == (
        "id,name,engine,description,is_sample,initial_sync_status,timezone,dbms_version\r\n"
        "7,bi_data,mysql,,False,complete,Asia/Shanghai,8.0.33\r\n"
        "15,quant_data,mysql,量化,False,complete,Asia/Shanghai,8.0.33\r\n"
    )


def test_databases_list_csv_fields_select_columns(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    result = cli(
        "--url",
        BASE,
        "--api-key",
        "k",
        "databases",
        "list",
        "--format",
        "csv",
        "--fields",
        "id,name",
    ).assert_ok(0)
    assert result.stdout == "id,name\r\n7,bi_data\r\n15,quant_data\r\n"


def test_databases_list_out_file(cli, mock_api, tmp_path):
    mock_api.get(f"{BASE}/api/database").mock(
        return_value=httpx.Response(200, json=DATABASES_PAYLOAD)
    )
    target = tmp_path / "dbs.json"
    result = cli(
        "--url", BASE, "--api-key", "k", "databases", "list", "--out", str(target)
    ).assert_ok(0)
    assert result.json() == {"out": str(target), "rows": 2, "truncated": False}
    assert json.loads(target.read_text())[0]["id"] == 7


def test_databases_list_500_exit_1(cli, mock_api):
    mock_api.get(f"{BASE}/api/database").mock(return_value=httpx.Response(500, text="down"))
    result = cli("--url", BASE, "--api-key", "k", "databases", "list")
    assert result.exit_code == 1
    assert result.error()["code"] == "server_error"


# -- verbose redaction ----------------------------------------------------------------


def test_verbose_logs_requests_but_redacts_secrets(cli, mock_api):
    mock_api.post(f"{BASE}/api/session").mock(return_value=httpx.Response(200, json={"id": "sid"}))
    mock_whoami(mock_api)
    result = cli(
        "--url",
        BASE,
        "--username",
        "user@example.com",
        "--password",
        "SUPER-SECRET-PW",
        "-v",
        "whoami",
    )
    result.assert_ok(0)
    assert "POST /api/session" in result.stderr
    assert "SUPER-SECRET-PW" not in result.stderr
    assert '"password": "***"' in result.stderr
    # the session id itself must never appear in the log either
    assert "sid" not in result.stderr.replace("session", "")


def test_verbose_api_key_never_logged(cli, mock_api):
    mock_whoami(mock_api)
    result = cli("--url", BASE, "--api-key", "SECRET-KEY-XYZ", "-v", "whoami")
    result.assert_ok(0)
    assert "SECRET-KEY-XYZ" not in result.stderr
    assert "request: GET /api/user/current" in result.stderr
    assert "response: 200 /api/user/current" in result.stderr


def test_quiet_by_default(cli, mock_api):
    mock_whoami(mock_api)
    result = cli("--url", BASE, "--api-key", "k", "whoami").assert_ok(0)
    assert result.stderr == ""
