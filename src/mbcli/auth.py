"""Authentication (DESIGN §3.2).

Two modes:
- API key: every request carries `X-API-Key` (no expiry, a 401 is final).
- Session: cache `{session_id, obtained_at}` at `Config.session_cache`, refresh
  via `POST /api/session` when absent, older than 14 days, or corrupt (silently
  ignored). Cache writes are atomic: tempfile in the same directory -> chmod
  0600 -> os.replace; the cache directory is created 0700.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from mbcli.config import Config
from mbcli.errors import MbcliError

SESSION_TTL_SECONDS = 14 * 24 * 3600
SESSION_PATH = "/api/session"

SENSITIVE_KEYS = frozenset(
    {"password", "api_key", "authorization", "x-api-key", "x-metabase-session"}
)


def redact_secrets(value: Any) -> Any:
    """Recursively replace credential values with '***' for verbose logging."""
    if isinstance(value, dict):
        return {
            key: ("***" if str(key).lower() in SENSITIVE_KEYS and item else redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


class AuthManager:
    def __init__(self, config: Config, log: Callable[[str], None] | None = None) -> None:
        self.config = config
        self._log = log
        self._session_id: str | None = None

    @property
    def uses_api_key(self) -> bool:
        return bool(self.config.api_key)

    def auth_headers(self) -> dict[str, str]:
        """Headers authenticating the next request (triggers login if needed)."""
        if self.config.api_key:
            return {"X-API-Key": self.config.api_key}
        return {"X-Metabase-Session": self._ensure_session()}

    def invalidate(self) -> None:
        """Forget the current session (memory + cache file)."""
        self._session_id = None
        try:
            Path(self.config.session_cache).expanduser().unlink(missing_ok=True)
        except OSError:
            pass

    # -- session cache ----------------------------------------------------

    def _read_cache(self) -> dict[str, Any] | None:
        try:
            data = json.loads(Path(self.config.session_cache).expanduser().read_text("utf-8"))
        except (OSError, ValueError):
            return None  # missing or corrupt: silently ignored
        if not isinstance(data, dict):
            return None
        session_id = data.get("session_id")
        obtained_at = data.get("obtained_at")
        if not isinstance(session_id, str) or not session_id:
            return None
        if not isinstance(obtained_at, int | float):
            return None
        if time.time() - obtained_at > SESSION_TTL_SECONDS:
            return None  # expired
        return {"session_id": session_id, "obtained_at": obtained_at}

    def _write_cache(self, session_id: str) -> None:
        path = Path(self.config.session_cache).expanduser()
        directory = path.parent
        directory_existed = directory.is_dir()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not directory_existed:
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
        fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"session_id": session_id, "obtained_at": time.time()}))
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    def _ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        cached = self._read_cache()
        if cached is not None:
            self._session_id = cached["session_id"]
            return self._session_id
        return self._login()

    # -- login ------------------------------------------------------------

    def _login(self) -> str:
        """POST /api/session. Any response-level failure -> auth_failed (ADR #15)."""
        self.config.require_credentials()
        payload = {"username": self.config.username, "password": self.config.password}
        if self._log:
            self._log(
                f"request: POST {SESSION_PATH}"
                f" body={json.dumps(redact_secrets(payload), ensure_ascii=False)}"
            )
        try:
            response = httpx.post(
                f"{self.config.url}{SESSION_PATH}",
                json=payload,
                timeout=self.config.httpx_timeout(),
                follow_redirects=False,
            )
        except httpx.TimeoutException as exc:
            raise MbcliError("timeout", f"login request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise MbcliError("network_error", f"login connection failed: {exc}") from exc
        if self._log:
            self._log(f"response: {response.status_code} {SESSION_PATH}")
        if response.status_code != 200:
            raise MbcliError(
                "auth_failed",
                f"login failed ({response.status_code}): {' '.join(response.text.split())[:200]}",
                detail={"http_status": response.status_code},
            )
        try:
            session_id = response.json().get("id")
        except ValueError as exc:
            raise MbcliError("auth_failed", "login response is not valid JSON") from exc
        if not isinstance(session_id, str) or not session_id:
            raise MbcliError("auth_failed", "login response missing session id")
        self._write_cache(session_id)
        self._session_id = session_id
        return session_id
