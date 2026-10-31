"""AuthManager (DESIGN §3.2): api-key mode, session cache lifecycle, atomic write."""

from __future__ import annotations

import json
import stat
import time
from pathlib import Path

import httpx
import pytest

from mbcli.auth import SESSION_TTL_SECONDS, AuthManager, redact_secrets
from mbcli.errors import MbcliError

LOGIN = "http://test.local/api/session"


def write_session(config, session_id: str, obtained_at: float) -> None:
    path = Path(config.session_cache)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": session_id, "obtained_at": obtained_at}))


# -- api key mode ---------------------------------------------------------------


def test_api_key_headers_without_login(make_config, mock_api):
    manager = AuthManager(make_config(api_key="key-1"))
    assert manager.auth_headers() == {"X-API-Key": "key-1"}
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid"}))
    assert login.call_count == 0


def test_api_key_mode_flag(make_config):
    assert AuthManager(make_config(api_key="k")).uses_api_key
    assert not AuthManager(make_config(username="u", password="p")).uses_api_key


def test_api_key_wins_over_credentials(make_config, mock_api):
    manager = AuthManager(make_config(api_key="key-1", username="u", password="p"))
    assert manager.auth_headers() == {"X-API-Key": "key-1"}


# -- session login + cache ---------------------------------------------------------


def test_login_writes_cache_0600(make_config, mock_api):
    config = make_config(username="u", password="p")
    mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid-1"}))
    headers = AuthManager(config).auth_headers()
    assert headers == {"X-Metabase-Session": "sid-1"}
    path = Path(config.session_cache)
    data = json.loads(path.read_text())
    assert data["session_id"] == "sid-1"
    assert data["obtained_at"] <= time.time()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_cache_roundtrip_no_second_login(make_config, mock_api):
    config = make_config(username="u", password="p")
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid-1"}))
    manager = AuthManager(config)
    assert manager.auth_headers() == {"X-Metabase-Session": "sid-1"}
    assert manager.auth_headers() == {"X-Metabase-Session": "sid-1"}
    assert login.call_count == 1


def test_valid_cache_file_skips_login(make_config, mock_api):
    config = make_config(username="u", password="p")
    write_session(config, "cached", time.time())
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "fresh"}))
    assert AuthManager(config).auth_headers() == {"X-Metabase-Session": "cached"}
    assert login.call_count == 0


def test_expired_cache_relogs_in(make_config, mock_api):
    config = make_config(username="u", password="p")
    write_session(config, "stale", time.time() - SESSION_TTL_SECONDS - 60)
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "fresh"}))
    assert AuthManager(config).auth_headers() == {"X-Metabase-Session": "fresh"}
    assert login.call_count == 1


def test_cache_just_under_ttl_still_valid(make_config, mock_api):
    config = make_config(username="u", password="p")
    write_session(config, "almost", time.time() - SESSION_TTL_SECONDS + 3600)
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "fresh"}))
    assert AuthManager(config).auth_headers() == {"X-Metabase-Session": "almost"}
    assert login.call_count == 0


@pytest.mark.parametrize(
    "content",
    [
        "not json at all {{{",
        "[]",
        '{"no_session_id": 1, "obtained_at": 123}',
        '{"session_id": "", "obtained_at": 123}',
        '{"session_id": "x", "obtained_at": "yesterday"}',
        '{"session_id": "x"}',
    ],
)
def test_corrupt_cache_silently_relogs_in(content, make_config, mock_api):
    config = make_config(username="u", password="p")
    Path(config.session_cache).write_text(content)
    login = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "fresh"}))
    assert AuthManager(config).auth_headers() == {"X-Metabase-Session": "fresh"}
    assert login.call_count == 1


def test_atomic_write_leaves_no_temp_files(make_config, mock_api):
    config = make_config(username="u", password="p")
    mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid"}))
    AuthManager(config).auth_headers()
    directory = Path(config.session_cache).parent
    assert sorted(p.name for p in directory.iterdir()) == ["session.json"]


def test_cache_directory_created_0700(make_config, mock_api, tmp_path):
    config = make_config(
        username="u", password="p", session_cache=str(tmp_path / "deep" / "mb" / "s.json")
    )
    mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid"}))
    AuthManager(config).auth_headers()
    assert stat.S_IMODE((tmp_path / "deep" / "mb").stat().st_mode) == 0o700


def test_invalidate_forgets_session_and_file(make_config, mock_api):
    config = make_config(username="u", password="p")
    write_session(config, "cached", time.time())
    manager = AuthManager(config)
    manager.auth_headers()
    manager.invalidate()
    assert not Path(config.session_cache).exists()
    mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "fresh"}))
    assert manager.auth_headers() == {"X-Metabase-Session": "fresh"}


def test_missing_credentials_at_login_raises_config_missing(make_config):
    config = make_config()  # no api key, no username/password
    with pytest.raises(MbcliError) as excinfo:
        AuthManager(config).auth_headers()
    assert excinfo.value.code == "config_missing"


# -- login failures (any response shape -> auth_failed, ADR #15) -----------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, text="Unauthenticated"),
        httpx.Response(400, json={"errors": {"username": "should not be blank"}}),
        httpx.Response(400, text="Bad Request"),
        httpx.Response(500, text="Internal Server Error"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"wrong": "shape"}),
        httpx.Response(200, json={"id": 12345}),
    ],
)
def test_login_failure_any_shape_is_auth_failed(response, make_config, mock_api):
    mock_api.post(LOGIN).mock(return_value=response)
    with pytest.raises(MbcliError) as excinfo:
        AuthManager(make_config(username="u", password="p")).auth_headers()
    assert excinfo.value.code == "auth_failed"
    assert excinfo.value.exit_code == 1


def test_login_transport_error_is_network_error(make_config, mock_api):
    mock_api.post(LOGIN).mock(side_effect=httpx.ConnectError("no route to host"))
    with pytest.raises(MbcliError) as excinfo:
        AuthManager(make_config(username="u", password="p")).auth_headers()
    assert excinfo.value.code == "network_error"


def test_login_timeout(make_config, mock_api):
    mock_api.post(LOGIN).mock(side_effect=httpx.ReadTimeout("too slow"))
    with pytest.raises(MbcliError) as excinfo:
        AuthManager(make_config(username="u", password="p")).auth_headers()
    assert excinfo.value.code == "timeout"


def test_login_request_body_and_session_header(make_config, mock_api):
    config = make_config(username="alice", password="pw")
    route = mock_api.post(LOGIN).mock(return_value=httpx.Response(200, json={"id": "sid"}))
    AuthManager(config).auth_headers()
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"username": "alice", "password": "pw"}


# -- redaction ------------------------------------------------------------------


def test_redact_secrets_nested():
    value = {
        "username": "alice",
        "password": "pw",
        "nested": [{"api_key": "k", "keep": 1}, {"X-Metabase-Session": "s"}],
    }
    redacted = redact_secrets(value)
    assert redacted["password"] == "***"
    assert redacted["nested"][0]["api_key"] == "***"
    assert redacted["nested"][1]["X-Metabase-Session"] == "***"
    assert redacted["username"] == "alice"
    assert redacted["nested"][0]["keep"] == 1


def test_redact_secrets_does_not_mutate_original():
    value = {"password": "pw"}
    redact_secrets(value)
    assert value["password"] == "pw"
