"""HTTP client (DESIGN §3.3): auth header injection, 401 re-login replay,
GET-only retries, and error normalization to MbcliError codes."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import httpx

from mbcli.auth import AuthManager, redact_secrets
from mbcli.config import Config
from mbcli.errors import MbcliError
from mbcli.reserved import find_reserved_token

DEFAULT_RETRY_BACKOFFS: tuple[float, ...] = (0.5, 1.0)
RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


def one_line(text: str) -> str:
    return " ".join(str(text).split())


class MetabaseClient:
    def __init__(
        self,
        config: Config,
        auth: AuthManager | None = None,
        log: Callable[[str], None] | None = None,
        retry_backoffs: Sequence[float] = DEFAULT_RETRY_BACKOFFS,
    ) -> None:
        self.config = config
        self.auth = auth or AuthManager(config, log=log)
        self.log = log
        self.retry_backoffs = tuple(retry_backoffs)
        self._http = httpx.Client(
            base_url=config.url,
            timeout=config.httpx_timeout(),
            follow_redirects=False,
        )

    # -- public API --------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        auth: bool = True,
        pass_status: Iterable[int] = (),
    ) -> Any:
        """GET and return deserialized JSON. Idempotent: retried on connect
        errors / 429 / 5xx (backoff 0.5s, 1s; max 2 retries)."""
        return self._execute(
            "GET", path, params=params, body=None, use_auth=auth, pass_status=tuple(pass_status)
        )

    def post_json(
        self,
        path: str,
        body: Any,
        *,
        auth: bool = True,
        pass_status: Iterable[int] = (),
    ) -> Any:
        """POST a JSON body and return deserialized JSON. Never retried."""
        return self._execute(
            "POST", path, params=None, body=body, use_auth=auth, pass_status=tuple(pass_status)
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> MetabaseClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- internals ----------------------------------------------------------

    def _execute(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        body: Any,
        use_auth: bool,
        pass_status: tuple[int, ...],
    ) -> Any:
        response = self._attempt(method, path, params, body, use_auth, pass_status)
        # 401 handling (§3.2): invalidate + re-login once + replay. The login
        # call itself never goes through this client, so it can never recurse.
        if use_auth and response.status_code == 401:
            if self.auth.uses_api_key:
                raise self._normalize_error(response, path)  # API key 401 is final
            self.auth.invalidate()
            self.auth.auth_headers()  # force re-login; failure -> auth_failed
            response = self._attempt(method, path, params, body, use_auth, pass_status)
        if response.status_code in pass_status:
            return self._parse_json(response)
        return self._checked(response, path)

    def _attempt(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        body: Any,
        use_auth: bool,
        pass_status: tuple[int, ...],
    ) -> httpx.Response:
        headers = self.auth.auth_headers() if use_auth else {}
        backoffs = self.retry_backoffs if method.upper() == "GET" else ()
        tries = 0
        while True:
            try:
                self._log_request(method, path, params, body)
                response = self._http.request(
                    method, path, params=params, json=body, headers=headers
                )
            except httpx.TimeoutException as exc:
                raise MbcliError("timeout", f"request timed out: {one_line(str(exc))}") from exc
            except httpx.ConnectError as exc:
                if tries < len(backoffs):
                    time.sleep(backoffs[tries])
                    tries += 1
                    continue
                raise MbcliError(
                    "network_error", f"connection failed: {one_line(str(exc))}"
                ) from exc
            except httpx.TransportError as exc:
                raise MbcliError("network_error", f"request failed: {one_line(str(exc))}") from exc
            self._log_response(response, path)
            if (
                response.status_code in RETRYABLE_STATUSES
                and response.status_code not in pass_status
                and tries < len(backoffs)
            ):
                time.sleep(backoffs[tries])
                tries += 1
                continue
            return response

    def _checked(self, response: httpx.Response, path: str) -> Any:
        if 200 <= response.status_code < 300:
            return self._parse_json(response)
        raise self._normalize_error(response, path)

    # -- error normalization (DESIGN §3.3 table) ---------------------------

    def _normalize_error(self, response: httpx.Response, path: str) -> MbcliError:
        status = response.status_code
        body = self._try_json(response)
        text = response.text.strip() if response.text else ""

        def body_message() -> str:
            if isinstance(body, dict):
                for key in ("error", "message"):
                    value = body.get(key)
                    if isinstance(value, str) and value.strip():
                        return one_line(value)
            return one_line(text)[:300] if text else ""

        detail: dict[str, Any] = {"http_status": status}

        if status == 401:
            return MbcliError(
                "auth_failed", body_message() or f"authentication failed ({status})", detail
            )
        if status == 403:
            message = body_message()
            return MbcliError(
                "permission_denied", message or f"permission denied ({status})", detail
            )
        if status == 404:
            message = body_message()
            return MbcliError("not_found", message or f"not found: {path}", detail)
        if status in (400, 422):
            if isinstance(body, dict):
                if "error" in body and body.get("status") == "failed":
                    for key in ("error_type", "error_code"):
                        if body.get(key) is not None:
                            detail[key] = body[key]
                    message = body_message() or "query failed"
                    # §13.4 / ADR #22: when the server error points at a MySQL
                    # reserved word, append a backtick hint to error.detail
                    # (user SQL is never rewritten — ADR #4).
                    reserved = find_reserved_token(message)
                    if reserved is not None:
                        detail["reserved_word"] = reserved
                        detail["hint"] = (
                            f"列名 `{reserved}` 为保留字，请用反引号包裹（MySQL 8.0 保留字表）"
                        )
                    return MbcliError("bad_query", message, detail)
                errors = body.get("errors")
                if isinstance(errors, dict):
                    detail["errors"] = errors
                    return MbcliError(
                        "invalid_request", "invalid request: field validation failed", detail
                    )
            return MbcliError(
                "invalid_request", body_message() or f"invalid request ({status})", detail
            )
        if status == 429:
            return MbcliError("rate_limited", body_message() or "rate limited (429)", detail)
        if status >= 500:
            return MbcliError("server_error", body_message() or f"server error ({status})", detail)
        return MbcliError(
            "invalid_request", body_message() or f"unexpected HTTP status {status}", detail
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _try_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _parse_json(response: httpx.Response) -> Any:
        text = response.text
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise MbcliError(
                "invalid_response",
                "response body is not valid JSON",
                detail={"http_status": response.status_code, "body": text[:200]},
            ) from exc

    def _log_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        body: Any,
    ) -> None:
        if not self.log:
            return
        line = f"request: {method} {path}"
        if params:
            line += f" params={params}"
        if body is not None:
            line += " body=" + json.dumps(redact_secrets(body), ensure_ascii=False, default=str)
        self.log(line)

    def _log_response(self, response: httpx.Response, path: str) -> None:
        if self.log:
            self.log(f"response: {response.status_code} {path}")
