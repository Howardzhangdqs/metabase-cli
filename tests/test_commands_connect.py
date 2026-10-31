"""Tests for connect/disconnect (ADR #18) and the --db default_db fallback.

All offline (respx) + tmp paths: MB_CONFIG_FILE always redirects to tmp, and a
local autouse fixture wipes the new MB_DEFAULT_DB/MB_CONFIG_FILE keys so a
developer's exported env can never leak into these tests.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

BASE = "http://test.local"

CURRENT_USER = {
    "id": 42,
    "email": "agent@example.com",
    "is_superuser": False,
    "group_ids": [1, 10],
    "first_name": "A",
}

DATABASES = {
    "data": [
        {"id": 15, "name": "quant_data"},
        {"id": 13, "name": "bi_data"},
    ],
    "total": 2,
}

METADATA_15 = {
    "id": 15,
    "name": "quant_data",
    "engine": "mysql",
    "tables": [
        {
            "id": 3258,
            "schema": "",
            "name": "app_mau",
            "display_name": "app_mau",
            "description": None,
            "estimated_row_count": 65,
            "active": True,
            "fields": [{"id": 37677, "name": "date", "database_type": "VARCHAR"}],
        }
    ],
}

QUERY_METADATA = {
    "fields": [
        {
            "id": 37677,
            "name": "date",
            "display_name": "date",
            "base_type": "type/Text",
            "semantic_type": None,
            "database_type": "VARCHAR",
            "database_position": 1,
        }
    ]
}

DATASET_OK = {
    "status": "completed",
    "data": {
        "cols": [{"name": "one", "base_type": "type/Integer", "database_type": "INT"}],
        "rows": [[1]],
    },
    "running_time": 5,
}


# -- local fixtures ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_new_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """conftest predates these keys; make sure a developer's exported
    MB_DEFAULT_DB / MB_CONFIG_FILE can never leak into these tests."""
    monkeypatch.delenv("MB_DEFAULT_DB", raising=False)
    monkeypatch.delenv("MB_CONFIG_FILE", raising=False)


@pytest.fixture
def cfg_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.toml"
    monkeypatch.setenv("MB_CONFIG_FILE", str(path))
    return path


@pytest.fixture
def cache_file() -> Path:
    """The session-cache path conftest's isolated_env already redirected to tmp."""
    return Path(os.environ["MB_SESSION_CACHE"])


def mock_login(mock_api, base: str = BASE):
    return mock_api.post(f"{base}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "sid-1"})
    )


def mock_whoami(mock_api, base: str = BASE):
    return mock_api.get(f"{base}/api/user/current").mock(
        return_value=httpx.Response(200, json=CURRENT_USER)
    )


def mock_databases(mock_api, base: str = BASE, payload=None):
    return mock_api.get(f"{base}/api/database").mock(
        return_value=httpx.Response(200, json=payload or DATABASES)
    )


def mock_query_endpoints(mock_api, base: str = BASE):
    """login + metadata + query_metadata + dataset: the full flagless stack."""
    mock_login(mock_api, base)
    mock_databases(mock_api, base)
    mock_api.get(f"{base}/api/database/15/metadata").mock(
        return_value=httpx.Response(200, json=METADATA_15)
    )
    mock_api.get(f"{base}/api/table/3258/query_metadata").mock(
        return_value=httpx.Response(200, json=QUERY_METADATA)
    )
    return mock_api.post(f"{base}/api/dataset").mock(
        return_value=httpx.Response(200, json=DATASET_OK)
    )


def write_connection(
    path: Path,
    *,
    url: str = BASE,
    username: str | None = "u@example.com",
    password: str | None = "pw",
    api_key: str | None = None,
    default_db: str | None = None,
) -> None:
    lines = ["[metabase]", f'url = "{url}"']
    if api_key:
        lines.append(f'api_key = "{api_key}"')
    else:
        lines.append(f'username = "{username}"')
        lines.append(f'password = "{password}"')
    if default_db is not None:
        lines.append(f'default_db = "{default_db}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def dataset_body(route) -> dict:
    return json.loads(route.calls.last.request.read())


# -- connect: happy paths --------------------------------------------------------------


def test_connect_password_mode_writes_toml_and_clears_cache(cli, mock_api, cfg_file, cache_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u@example.com",
        "--password",
        "pw",
        "--non-interactive",
    ).assert_ok(0)
    assert result.json() == {
        "connected": True,
        "url": BASE,
        "user": {"id": 42, "email": "agent@example.com"},
        "default_db": None,
        "replaced": None,
        "config_file": str(cfg_file),
    }
    # exact [metabase] section (key order: url, username, password)
    assert cfg_file.read_text(encoding="utf-8") == (
        '[metabase]\nurl = "http://test.local"\nusername = "u@example.com"\npassword = "pw"\n'
    )
    assert stat.S_IMODE(cfg_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg_file.parent.stat().st_mode) == 0o700
    # the login during validation wrote a cache; connect must invalidate it
    assert not cache_file.exists()


def test_connect_api_key_mode_skips_login(cli, mock_api, cfg_file):
    login = mock_login(mock_api)
    whoami = mock_whoami(mock_api)
    cli("connect", "--url", BASE, "--api-key", "key", "--non-interactive").assert_ok(0)
    assert login.call_count == 0
    assert whoami.calls.last.request.headers["X-API-Key"] == "key"
    assert cfg_file.read_text(encoding="utf-8") == (
        '[metabase]\nurl = "http://test.local"\napi_key = "key"\n'
    )


def test_connect_root_level_flags_honored(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    cli(
        "--url",
        BASE,
        "--username",
        "u@example.com",
        "--password",
        "pw",
        "connect",
        "--non-interactive",
    ).assert_ok(0)
    assert "url" in cfg_file.read_text(encoding="utf-8")


def test_connect_env_fills_missing_values(cli, mock_api, cfg_file, monkeypatch):
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "u@example.com")
    cli("connect", "--password", "pw", "--non-interactive").assert_ok(0)
    assert "pw" in cfg_file.read_text(encoding="utf-8")


# -- connect: validation before save ------------------------------------------------------


def test_connect_bad_login_writes_nothing(cli, mock_api, cfg_file):
    mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(401, text="Unauthenticated")
    )
    result = cli(
        "connect", "--url", BASE, "--username", "u", "--password", "wrong", "--non-interactive"
    )
    assert result.exit_code == 1
    assert result.error()["code"] == "auth_failed"
    assert not cfg_file.exists()


def test_connect_default_db_by_name(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "quant_data",
        "--non-interactive",
    ).assert_ok(0)
    assert result.json()["default_db"] == {"id": 15, "name": "quant_data"}
    assert 'default_db = "15"' in cfg_file.read_text(encoding="utf-8")  # stores the id


def test_connect_default_db_numeric_id_validated(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "15",
        "--non-interactive",
    ).assert_ok(0)
    assert result.json()["default_db"] == {"id": 15, "name": "quant_data"}


def test_connect_default_db_unknown_id_not_found(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "999",
        "--non-interactive",
    )
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"
    assert not cfg_file.exists()


def test_connect_default_db_unknown_name_not_found(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "nope_db",
        "--non-interactive",
    )
    assert result.exit_code == 1
    assert result.error()["code"] == "not_found"
    assert not cfg_file.exists()


def test_connect_default_db_ambiguous_name(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api, payload={"data": [{"id": 1, "name": "dup"}, {"id": 2, "name": "dup"}]})
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "dup",
        "--non-interactive",
    )
    assert result.exit_code == 1
    assert result.error()["code"] == "ambiguous_ref"
    assert not cfg_file.exists()


# -- connect: interactive prompts -----------------------------------------------------------


def test_connect_prompts_for_url_username_hidden_password(cli, mock_api, cfg_file, monkeypatch):
    mock_login(mock_api)
    mock_whoami(mock_api)
    calls: list[tuple[str, dict]] = []
    answers = {"Metabase URL": BASE, "Username": "u@example.com", "Password": "pw"}

    def fake_prompt(text: str = "", **kwargs):
        calls.append((text, kwargs))
        return answers[text]

    monkeypatch.setattr("typer.prompt", fake_prompt)
    cli("connect").assert_ok(0)
    assert [text for text, _ in calls] == ["Metabase URL", "Username", "Password"]
    assert calls[2][1].get("hide_input") is True  # password input is hidden
    assert "pw" in cfg_file.read_text(encoding="utf-8")


def test_connect_prompts_only_missing_password(cli, mock_api, cfg_file, monkeypatch):
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "u@example.com")
    calls: list[tuple[str, dict]] = []

    def fake_prompt(text: str = "", **kwargs):
        calls.append((text, kwargs))
        return "pw"

    monkeypatch.setattr("typer.prompt", fake_prompt)
    cli("connect").assert_ok(0)
    assert [text for text, _ in calls] == ["Password"]
    assert calls[0][1].get("hide_input") is True


def test_connect_non_interactive_missing_exit_2(cli, mock_api, cfg_file):
    """--non-interactive never touches stdin: anything missing is invalid_args."""
    result = cli("connect", "--non-interactive")
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    for piece in ("--url", "--username", "--password"):
        assert piece in error["message"]


def test_connect_non_interactive_missing_url_only(cli, mock_api, cfg_file):
    result = cli("connect", "--username", "u", "--password", "p", "--non-interactive")
    assert result.exit_code == 2
    assert "--url" in result.error()["message"]
    assert not cfg_file.exists()


# -- connect: overwrite protection ----------------------------------------------------------


def test_connect_refuses_different_connection_without_force(cli, mock_api, cfg_file):
    write_connection(cfg_file, url="http://old.local", username="old")
    before = cfg_file.read_text(encoding="utf-8")
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect", "--url", BASE, "--username", "u", "--password", "p", "--non-interactive"
    )
    assert result.exit_code == 2
    error = result.error()
    assert error["code"] == "invalid_args"
    assert "--force" in error["message"]
    assert cfg_file.read_text(encoding="utf-8") == before  # file untouched


def test_connect_force_replaces_and_reports_old_url(cli, mock_api, cfg_file):
    write_connection(cfg_file, url="http://old.local", username="old", password="oldpw")
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--non-interactive",
        "--force",
    ).assert_ok(0)
    assert result.json()["replaced"] == "http://old.local"
    text = cfg_file.read_text(encoding="utf-8")
    assert BASE in text and "old.local" not in text


def test_connect_same_connection_reconnect_allowed_after_password_change(cli, mock_api, cfg_file):
    write_connection(cfg_file, url=BASE, username="u", password="OLD")
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect", "--url", BASE, "--username", "u", "--password", "NEW", "--non-interactive"
    ).assert_ok(0)  # no --force needed: same url + same username
    assert result.json()["replaced"] == BASE
    assert 'password = "NEW"' in cfg_file.read_text(encoding="utf-8")


def test_connect_same_api_key_reconnect_allowed(cli, mock_api, cfg_file):
    write_connection(cfg_file, url=BASE, api_key="k1")
    mock_whoami(mock_api)
    cli("connect", "--url", BASE, "--api-key", "k1", "--non-interactive").assert_ok(0)


def test_connect_api_key_change_needs_force(cli, mock_api, cfg_file):
    write_connection(cfg_file, url=BASE, api_key="k1")
    mock_whoami(mock_api)
    result = cli("connect", "--url", BASE, "--api-key", "k2", "--non-interactive")
    assert result.exit_code == 2
    assert result.error()["code"] == "invalid_args"


def test_connect_mode_switch_needs_force(cli, mock_api, cfg_file):
    """Saved api-key connection -> password connection is a DIFFERENT identity."""
    write_connection(cfg_file, url=BASE, api_key="k1")
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect", "--url", BASE, "--username", "u", "--password", "p", "--non-interactive"
    )
    assert result.exit_code == 2
    assert "--force" in result.error()["message"]


# -- connect: toml safety / round-trip -------------------------------------------------------


def test_connect_toml_round_trip_via_load_config(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    mock_databases(mock_api)
    cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--default-db",
        "quant_data",
        "--non-interactive",
    ).assert_ok(0)
    from mbcli.config import load_config

    config = load_config(None, env={}, dotenv_path=cfg_file.parent / "none.env", toml_path=cfg_file)
    assert config.url == BASE
    assert config.username == "u"
    assert config.password == "p"
    assert config.default_db == "15"


def test_connect_escapes_toml_specials(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    tricky = 'p"w\\d\n中'
    cli(
        "connect", "--url", BASE, "--username", "u", "--password", tricky, "--non-interactive"
    ).assert_ok(0)
    from mbcli.config import load_config

    config = load_config(None, env={}, dotenv_path=cfg_file.parent / "none.env", toml_path=cfg_file)
    assert config.password == tricky


def test_connect_clears_stale_session_cache(cli, mock_api, cfg_file, cache_file):
    cache_file.write_text(json.dumps({"session_id": "stale", "obtained_at": 99999999999}))
    mock_login(mock_api)
    mock_whoami(mock_api)
    cli(
        "connect", "--url", BASE, "--username", "u", "--password", "p", "--non-interactive"
    ).assert_ok(0)
    assert not cache_file.exists()


def test_connect_never_prints_secrets(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "SUPERSECRET123",
        "--non-interactive",
    ).assert_ok(0)
    assert "SUPERSECRET123" not in result.stdout + result.stderr


# -- connect: per-field flag > env > prompt (user amendment) ------------------------------


def test_connect_non_interactive_env_only_password_mode(cli, mock_api, cfg_file, monkeypatch):
    """MB_* env alone satisfies every field: --non-interactive succeeds, no flags."""
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "u@example.com")
    monkeypatch.setenv("MB_PASSWORD", "pw")
    result = cli("connect", "--non-interactive").assert_ok(0)
    assert result.json()["connected"] is True
    assert 'password = "pw"' in cfg_file.read_text(encoding="utf-8")


def test_connect_non_interactive_env_only_api_key_mode(cli, mock_api, cfg_file, monkeypatch):
    login = mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_API_KEY", "key2")
    cli("connect", "--non-interactive").assert_ok(0)
    assert login.call_count == 0  # env key wins: session login never happens
    assert 'api_key = "key2"' in cfg_file.read_text(encoding="utf-8")


def test_connect_no_prompt_when_flag_and_env_cover_everything(cli, mock_api, cfg_file, monkeypatch):
    """Prompt fires ONLY when a field is absent from both flag and env."""
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "u@example.com")

    def no_prompts(text: str = "", **kwargs):  # pragma: no cover - must not run
        raise AssertionError(f"unexpected prompt: {text!r}")

    monkeypatch.setattr("typer.prompt", no_prompts)
    cli("connect", "--password", "pw").assert_ok(0)


def test_connect_mixed_flag_and_env_fields(cli, mock_api, cfg_file, monkeypatch):
    """Per-field resolution: flag username + env url/password, nothing else."""
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_PASSWORD", "pw")
    cli("connect", "--username", "flag-user@example.com", "--non-interactive").assert_ok(0)
    text = cfg_file.read_text(encoding="utf-8")
    assert 'username = "flag-user@example.com"' in text  # flag beats env for its field
    assert 'password = "pw"' in text  # env filled the untouched field


def test_connect_env_missing_password_still_prompts_hidden(cli, mock_api, cfg_file, monkeypatch):
    """Flag+env both absent for ONE field -> interactive prompt for just that field."""
    mock_login(mock_api)
    mock_whoami(mock_api)
    monkeypatch.setenv("MB_URL", BASE)
    monkeypatch.setenv("MB_USERNAME", "u@example.com")
    calls: list[tuple[str, dict]] = []

    def fake_prompt(text: str = "", **kwargs):
        calls.append((text, kwargs))
        return "pw"

    monkeypatch.setattr("typer.prompt", fake_prompt)
    cli("connect").assert_ok(0)
    assert [text for text, _ in calls] == ["Password"]  # only the truly missing field
    assert calls[0][1].get("hide_input") is True


# -- connect: anti-leak in logs and errors (user amendment) --------------------------------


def test_connect_verbose_logs_mask_password(cli, mock_api, cfg_file):
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "-v",
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "PW-SUPERSECRET",
        "--non-interactive",
    ).assert_ok(0)
    assert "POST /api/session" in result.stderr  # the login WAS logged…
    assert '"password": "***"' in result.stderr  # …with the password masked…
    assert "PW-SUPERSECRET" not in result.stdout + result.stderr  # …never echoed


def test_connect_verbose_never_logs_api_key(cli, mock_api, cfg_file):
    mock_whoami(mock_api)
    result = cli(
        "-v",
        "connect",
        "--url",
        BASE,
        "--api-key",
        "KEY-SUPERSECRET",
        "--non-interactive",
    ).assert_ok(0)
    assert "request: GET /api/user/current" in result.stderr  # requests were logged…
    assert "KEY-SUPERSECRET" not in result.stdout + result.stderr  # …but secrets never


def test_connect_refuse_error_hides_stored_secrets(cli, mock_api, cfg_file):
    """The overwrite-refused error echoes only the old URL — never the stored
    username/password/api_key."""
    write_connection(cfg_file, url="http://old.local", username="u2", password="pw")
    mock_login(mock_api)
    mock_whoami(mock_api)
    result = cli(
        "connect",
        "--url",
        BASE,
        "--username",
        "u",
        "--password",
        "p",
        "--non-interactive",
    )
    assert result.exit_code == 2
    assert "old.local" in result.stderr  # URL is fine to echo…
    for secret in ("u2", "pw"):
        assert secret not in result.stderr  # …stored identity secrets are not


# -- disconnect -----------------------------------------------------------------------------


def test_disconnect_removes_section_and_cache(cli, mock_api, cfg_file, cache_file):
    write_connection(cfg_file)
    cache_file.write_text(json.dumps({"session_id": "s", "obtained_at": 1}))
    result = cli("disconnect").assert_ok(0)
    assert result.json() == {
        "disconnected": True,
        "was_connected": True,
        "session_cache_cleared": True,
    }
    assert not cfg_file.exists()  # [metabase] was the only content
    assert not cache_file.exists()


def test_disconnect_idempotent(cli, mock_api, cfg_file):
    write_connection(cfg_file)
    cli("disconnect").assert_ok(0)
    result = cli("disconnect").assert_ok(0)
    assert result.json() == {
        "disconnected": True,
        "was_connected": False,
        "session_cache_cleared": False,
    }


def test_disconnect_keeps_other_toml_content(cli, mock_api, cfg_file):
    cfg_file.write_text(
        f'[metabase]\nurl = "{BASE}"\nusername = "u"\n[other]\nkeep = 1\n',
        encoding="utf-8",
    )
    result = cli("disconnect").assert_ok(0)
    assert result.json()["was_connected"] is True
    text = cfg_file.read_text(encoding="utf-8")
    assert "[other]" in text and "keep = 1" in text
    assert "[metabase]" not in text and "username" not in text


def test_disconnect_without_anything_exit_0(cli, mock_api):
    result = cli("disconnect").assert_ok(0)
    assert result.json() == {
        "disconnected": True,
        "was_connected": False,
        "session_cache_cleared": False,
    }


def test_disconnect_honors_mb_config_file(cli, mock_api, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere.toml"
    write_connection(elsewhere)
    monkeypatch.setenv("MB_CONFIG_FILE", str(elsewhere))
    cli("disconnect").assert_ok(0)
    assert not elsewhere.exists()


# -- --db fallback: config default_db / MB_DEFAULT_DB ---------------------------------------


def test_query_flagless_uses_toml_default_db(cli, mock_api, cfg_file):
    write_connection(cfg_file, default_db="15")
    dataset = mock_query_endpoints(mock_api)
    result = cli("query", "--native", "SELECT 1 AS one").assert_ok(0)  # zero flags!
    assert result.json()["rows"] == [[1]]
    assert dataset_body(dataset)["database"] == 15


def test_query_env_default_db_beats_toml(cli, mock_api, cfg_file, monkeypatch):
    write_connection(cfg_file, default_db="15")
    dataset = mock_query_endpoints(mock_api)
    monkeypatch.setenv("MB_DEFAULT_DB", "13")
    cli("query", "--native", "SELECT 1 AS one").assert_ok(0)
    assert dataset_body(dataset)["database"] == 13


def test_query_cli_db_beats_env_and_toml(cli, mock_api, cfg_file, monkeypatch):
    write_connection(cfg_file, default_db="15")
    dataset = mock_query_endpoints(mock_api)
    monkeypatch.setenv("MB_DEFAULT_DB", "13")
    cli("query", "--db", "7", "--native", "SELECT 1 AS one").assert_ok(0)
    assert dataset_body(dataset)["database"] == 7


def test_query_env_default_db_without_toml_value(cli, mock_api, cfg_file, monkeypatch):
    write_connection(cfg_file)  # connected, but no default_db saved
    dataset = mock_query_endpoints(mock_api)
    monkeypatch.setenv("MB_DEFAULT_DB", "15")
    cli("query", "--native", "SELECT 1 AS one").assert_ok(0)
    assert dataset_body(dataset)["database"] == 15


def test_query_no_db_anywhere_exit_2_with_hint(cli, mock_api, cfg_file):
    write_connection(cfg_file)
    dataset = mock_query_endpoints(mock_api)
    result = cli("query", "--native", "SELECT 1 AS one")
    assert result.exit_code == 2
    message = result.error()["message"]
    assert "--db" in message
    assert "mbcli connect --default-db" in message
    assert "MB_DEFAULT_DB" in message
    assert dataset.call_count == 0  # refused before any server round-trip


def test_schemas_flagless_default_db_resolves_by_name(cli, mock_api, cfg_file):
    write_connection(cfg_file, default_db="quant_data")
    mock_query_endpoints(mock_api)
    result = cli("schemas").assert_ok(0)
    assert result.json() == [{"name": "", "table_count": 1}]


def test_tables_flagless_default_db(cli, mock_api, cfg_file):
    write_connection(cfg_file, default_db="quant_data")
    mock_query_endpoints(mock_api)
    result = cli("tables").assert_ok(0)
    assert [t["name"] for t in result.json()] == ["app_mau"]


def test_context_flagless_default_db(cli, mock_api, cfg_file):
    write_connection(cfg_file, default_db="quant_data")
    mock_query_endpoints(mock_api)
    result = cli("context").assert_ok(0)
    assert result.stdout.startswith("# Database: quant_data (mysql, id=15)")


def test_fields_table_name_with_default_db(cli, mock_api, cfg_file):
    write_connection(cfg_file, default_db="quant_data")
    mock_query_endpoints(mock_api)
    result = cli("fields", "--table", "app_mau").assert_ok(0)
    assert result.json()[0]["name"] == "date"


def test_fields_numeric_table_id_needs_no_db(cli, mock_api, cfg_file):
    write_connection(cfg_file)  # no default_db at all
    listing = mock_query_endpoints(mock_api)
    result = cli("fields", "--table", "3258").assert_ok(0)
    assert result.json()[0]["name"] == "date"
    assert listing.call_count == 0  # numeric table id: no db listing lookup


def test_fields_table_name_missing_db_exit_2(cli, mock_api, cfg_file):
    write_connection(cfg_file)  # no default_db
    mock_query_endpoints(mock_api)
    result = cli("fields", "--table", "app_mau")
    assert result.exit_code == 2
    message = result.error()["message"]
    assert "mbcli connect --default-db" in message and "MB_DEFAULT_DB" in message


# -- config helpers (direct units) -----------------------------------------------------------


def test_load_config_resolves_default_db_and_config_file(tmp_path, monkeypatch):
    from mbcli.config import default_toml_path, load_config

    toml = tmp_path / "c.toml"
    write_connection(toml, default_db="15")
    monkeypatch.setenv("MB_CONFIG_FILE", str(toml))
    monkeypatch.delenv("MB_DEFAULT_DB", raising=False)
    config = load_config(None, dotenv_path=tmp_path / "n.env")
    assert config.config_file == str(toml)
    assert config.default_db == "15"
    assert str(default_toml_path())  # default path helper still exported

    monkeypatch.setenv("MB_DEFAULT_DB", "13")
    assert load_config(None, dotenv_path=tmp_path / "n.env").default_db == "13"  # env wins


def test_save_and_remove_metabase_section_units(tmp_path):
    from mbcli.config import remove_metabase_section, save_metabase_section

    path = tmp_path / "section.toml"
    save_metabase_section(path, {"url": "http://x", "password": None, "default_db": "15"})
    assert path.read_text(encoding="utf-8") == (
        '[metabase]\nurl = "http://x"\ndefault_db = "15"\n'  # None values skipped
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert remove_metabase_section(path) is True
    assert not path.exists()  # nothing else in the file -> deleted

    mixed = tmp_path / "mixed.toml"
    mixed.write_text("# top comment\n[metabase]\nurl = 'http://x'\n[other]\nkeep = 1\n")
    assert remove_metabase_section(mixed) is True
    text = mixed.read_text(encoding="utf-8")
    assert (
        "[other]" in text
        and "keep = 1" in text
        and "metabase" not in text.replace("# top comment", "")
    )

    assert remove_metabase_section(tmp_path / "missing.toml") is False
    plain = tmp_path / "plain.toml"
    plain.write_text("[other]\nkeep = 1\n")
    assert remove_metabase_section(plain) is False  # no [metabase] section
    assert plain.read_text(encoding="utf-8") == "[other]\nkeep = 1\n"
