"""MetabaseClient (DESIGN §3.3): error normalization matrix, 401 re-login replay,
GET-only retries — all offline via respx."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from mbcli.client import MetabaseClient
from mbcli.config import Config
from mbcli.errors import MbcliError

BASE = "http://test.local"
NO_RETRY = {"retry_backoffs": ()}


def api_client(**overrides) -> MetabaseClient:
    params = {**NO_RETRY, **overrides}
    return MetabaseClient(
        Config(url=BASE, api_key="key-1", session_cache="/tmp/none.json"), **params
    )


def seed_session(tmp_path: Path, session_id: str = "cached-sid") -> Config:
    config = Config(
        url=BASE,
        username="u",
        password="p",
        session_cache=str(tmp_path / "session.json"),
    )
    Path(config.session_cache).write_text(
        json.dumps({"session_id": session_id, "obtained_at": time.time()})
    )
    return config


# -- error normalization matrix (§3.3, parametrized) -----------------------------


@pytest.mark.parametrize(
    ("status", "body", "expected_code", "expected_detail"),
    [
        (401, "Unauthenticated", "auth_failed", {"http_status": 401}),
        (401, {"error": "Invalid API Key"}, "auth_failed", {"http_status": 401}),
        (403, "Forbidden", "permission_denied", {"http_status": 403}),
        (403, {"error": "You don't have permissions."}, "permission_denied", {"http_status": 403}),
        (404, "Not found.", "not_found", {"http_status": 404}),
        (404, {"error": "nope"}, "not_found", {"http_status": 404}),
        (
            400,
            {
                "error": "Table 'quant_data.x' doesn't exist",
                "status": "failed",
                "error_type": "invalidQuery",
            },
            "bad_query",
            {"http_status": 400, "error_type": "invalidQuery"},
        ),
        (
            400,
            {
                "error": "boom",
                "status": "failed",
                "error_type": "invalidQuery",
                "error_code": "qp6x",
            },
            "bad_query",
            {"http_status": 400, "error_type": "invalidQuery", "error_code": "qp6x"},
        ),
        (
            400,
            {"errors": {"db": "value must be an integer greater than zero."}},
            "invalid_request",
            {"http_status": 400, "errors": {"db": "value must be an integer greater than zero."}},
        ),
        (400, "plain text bad request", "invalid_request", {"http_status": 400}),
        (400, {"error": "no status field"}, "invalid_request", {"http_status": 400}),
        (
            422,
            {"errors": {"name": "should not be blank"}},
            "invalid_request",
            {"http_status": 422, "errors": {"name": "should not be blank"}},
        ),
        (429, {"error": "slow down"}, "rate_limited", {"http_status": 429}),
        (500, "Internal Server Error", "server_error", {"http_status": 500}),
        (502, {"error": "bad gateway"}, "server_error", {"http_status": 502}),
        (503, "initializing", "server_error", {"http_status": 503}),
        (200, "definitely not json", "invalid_response", {"http_status": 200}),
        (201, "[1, 2,", "invalid_response", {"http_status": 201}),
    ],
)
def test_error_normalization_matrix(mock_api, status, body, expected_code, expected_detail):
    if isinstance(body, str):
        response = httpx.Response(status, text=body)
    else:
        response = httpx.Response(status, json=body)
    mock_api.get(f"{BASE}/api/thing").mock(return_value=response)
    with pytest.raises(MbcliError) as excinfo:
        api_client().get("/api/thing")
    assert excinfo.value.code == expected_code
    assert excinfo.value.exit_code == 1
    for key, value in expected_detail.items():
        assert excinfo.value.detail[key] == value


def test_bad_query_message_comes_from_error_field(mock_api):
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(
            400, json={"error": "Table 'x' doesn't exist", "status": "failed"}
        )
    )
    with pytest.raises(MbcliError) as excinfo:
        api_client().post_json("/api/dataset", {"database": 1})
    assert excinfo.value.code == "bad_query"
    assert "Table 'x' doesn't exist" in excinfo.value.message


def test_invalid_request_detail_carries_field_errors(mock_api):
    errors = {"native": "value must be a map"}
    mock_api.post(f"{BASE}/api/dataset").mock(
        return_value=httpx.Response(400, json={"errors": errors})
    )
    with pytest.raises(MbcliError) as excinfo:
        api_client().post_json("/api/dataset", {})
    assert excinfo.value.detail["errors"] == errors


def test_success_returns_parsed_json(mock_api):
    mock_api.get(f"{BASE}/api/ok").mock(return_value=httpx.Response(200, json={"answer": 42}))
    assert api_client().get("/api/ok") == {"answer": 42}


def test_api_key_header_injected(mock_api):
    route = mock_api.get(f"{BASE}/api/who").mock(return_value=httpx.Response(200, json={}))
    api_client().get("/api/who")
    assert route.calls.last.request.headers["X-API-Key"] == "key-1"


def test_unauthenticated_call_sends_no_auth_headers(mock_api):
    route = mock_api.get(f"{BASE}/api/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    client = MetabaseClient(Config(url=BASE), **NO_RETRY)
    client.get("/api/health", auth=False)
    request = route.calls.last.request
    assert "X-API-Key" not in request.headers
    assert "X-Metabase-Session" not in request.headers


# -- retries: idempotent GET only ---------------------------------------------------


def test_get_429_then_success_retries(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(
        side_effect=[httpx.Response(429, json={}), httpx.Response(200, json={"ok": 1})]
    )
    assert api_client(retry_backoffs=(0, 0)).get("/api/r") == {"ok": 1}
    assert route.call_count == 2


def test_get_5xx_then_success_retries(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(
        side_effect=[httpx.Response(500, text="x"), httpx.Response(200, json={"ok": 1})]
    )
    assert api_client(retry_backoffs=(0, 0)).get("/api/r") == {"ok": 1}
    assert route.call_count == 2


def test_get_connect_error_then_success_retries(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, json={"ok": 1})]
    )
    assert api_client(retry_backoffs=(0, 0)).get("/api/r") == {"ok": 1}
    assert route.call_count == 2


def test_get_gives_up_after_two_retries(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(return_value=httpx.Response(500, text="down"))
    with pytest.raises(MbcliError) as excinfo:
        api_client(retry_backoffs=(0, 0)).get("/api/r")
    assert excinfo.value.code == "server_error"
    assert route.call_count == 3  # 1 attempt + 2 retries


def test_get_429_exhausted_raises_rate_limited(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(return_value=httpx.Response(429, text="slow"))
    with pytest.raises(MbcliError) as excinfo:
        api_client(retry_backoffs=(0, 0)).get("/api/r")
    assert excinfo.value.code == "rate_limited"
    assert route.call_count == 3


def test_post_never_retried_on_429(mock_api):
    route = mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(429, text="slow"))
    with pytest.raises(MbcliError) as excinfo:
        api_client().post_json("/api/dataset", {"q": 1})
    assert excinfo.value.code == "rate_limited"
    assert route.call_count == 1


def test_post_never_retried_on_5xx(mock_api):
    route = mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(502, text="bad"))
    with pytest.raises(MbcliError) as excinfo:
        api_client().post_json("/api/dataset", {"q": 1})
    assert excinfo.value.code == "server_error"
    assert route.call_count == 1


def test_post_connect_error_not_retried(mock_api):
    route = mock_api.post(f"{BASE}/api/dataset").mock(side_effect=httpx.ConnectError("nope"))
    with pytest.raises(MbcliError) as excinfo:
        api_client().post_json("/api/dataset", {"q": 1})
    assert excinfo.value.code == "network_error"
    assert route.call_count == 1


def test_read_timeout_not_retried(mock_api):
    route = mock_api.get(f"{BASE}/api/slow").mock(side_effect=httpx.ReadTimeout("too slow"))
    with pytest.raises(MbcliError) as excinfo:
        api_client(retry_backoffs=(0, 0)).get("/api/slow")
    assert excinfo.value.code == "timeout"
    assert route.call_count == 1


def test_get_connect_error_exhausted(mock_api):
    route = mock_api.get(f"{BASE}/api/r").mock(side_effect=httpx.ConnectError("unreachable"))
    with pytest.raises(MbcliError) as excinfo:
        api_client(retry_backoffs=(0, 0)).get("/api/r")
    assert excinfo.value.code == "network_error"
    assert route.call_count == 3


# -- 401: invalidate -> re-login once -> replay (§3.2) -------------------------------


def test_401_session_relogin_replay_succeeds(mock_api, tmp_path):
    config = seed_session(tmp_path, "stale-sid")
    login = mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "fresh"})
    )
    route = mock_api.get(f"{BASE}/api/who").mock(
        side_effect=[
            httpx.Response(401, text="Unauthenticated"),
            httpx.Response(200, json={"id": 1}),
        ]
    )
    client = MetabaseClient(config, retry_backoffs=())
    assert client.get("/api/who") == {"id": 1}
    assert login.call_count == 1
    assert route.call_count == 2
    assert route.calls[0].request.headers["X-Metabase-Session"] == "stale-sid"
    assert route.calls[1].request.headers["X-Metabase-Session"] == "fresh"


def test_401_replay_still_401_is_auth_failed_no_recursion(mock_api, tmp_path):
    config = seed_session(tmp_path)
    login = mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "fresh"})
    )
    route = mock_api.get(f"{BASE}/api/who").mock(
        return_value=httpx.Response(401, text="Unauthenticated")
    )
    with pytest.raises(MbcliError) as excinfo:
        MetabaseClient(config, retry_backoffs=()).get("/api/who")
    assert excinfo.value.code == "auth_failed"
    assert login.call_count == 1  # exactly one re-login
    assert route.call_count == 2  # original + exactly one replay


def test_401_relogin_fails_is_auth_failed(mock_api, tmp_path):
    config = seed_session(tmp_path)
    mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(400, json={"errors": {"password": "invalid"}})
    )
    mock_api.get(f"{BASE}/api/who").mock(return_value=httpx.Response(401, text="Unauthenticated"))
    with pytest.raises(MbcliError) as excinfo:
        MetabaseClient(config, retry_backoffs=()).get("/api/who")
    assert excinfo.value.code == "auth_failed"


def test_401_api_key_mode_fails_immediately_no_replay(mock_api):
    route = mock_api.get(f"{BASE}/api/who").mock(return_value=httpx.Response(401, text="nope"))
    with pytest.raises(MbcliError) as excinfo:
        api_client().get("/api/who")
    assert excinfo.value.code == "auth_failed"
    assert route.call_count == 1  # no replay in api-key mode


def test_401_post_replayed_once_too(mock_api, tmp_path):
    config = seed_session(tmp_path)
    mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "fresh"})
    )
    route = mock_api.post(f"{BASE}/api/dataset").mock(
        side_effect=[
            httpx.Response(401, text="Unauthenticated"),
            httpx.Response(200, json={"status": "completed"}),
        ]
    )
    assert MetabaseClient(config, retry_backoffs=()).post_json("/api/dataset", {}) == {
        "status": "completed"
    }
    assert route.call_count == 2


def test_stale_cache_file_removed_after_invalidate(mock_api, tmp_path):
    config = seed_session(tmp_path)
    mock_api.post(f"{BASE}/api/session").mock(
        return_value=httpx.Response(200, json={"id": "fresh"})
    )
    mock_api.get(f"{BASE}/api/who").mock(
        side_effect=[httpx.Response(401, text="x"), httpx.Response(200, json={})]
    )
    MetabaseClient(config, retry_backoffs=()).get("/api/who")
    data = json.loads(Path(config.session_cache).read_text())
    assert data["session_id"] == "fresh"


# -- pass_status (health) --------------------------------------------------------------


def test_pass_status_returns_body_without_error(mock_api):
    route = mock_api.get(f"{BASE}/api/health").mock(
        return_value=httpx.Response(503, json={"status": "initializing", "progress": 30})
    )
    assert api_client().get("/api/health", auth=False, pass_status=(503,)) == {
        "status": "initializing",
        "progress": 30,
    }
    assert route.call_count == 1  # pass_status statuses are not retried


# -- verbose logging redaction -----------------------------------------------------------


def test_verbose_log_redacts_body_password(mock_api):
    lines: list[str] = []
    client = MetabaseClient(
        Config(url=BASE, api_key="key-1", session_cache="/tmp/none.json"),
        log=lines.append,
        retry_backoffs=(),
    )
    mock_api.post(f"{BASE}/api/dataset").mock(return_value=httpx.Response(200, json={}))
    client.post_json("/api/dataset", {"native": {"query": "SELECT 1"}, "password": "pw"})
    joined = "\n".join(lines)
    assert "POST /api/dataset" in joined
    assert "pw" not in joined
    assert '"password": "***"' in joined
