"""Error codes, exit codes and the single-line stderr JSON contract (DESIGN §3.3)."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

# DESIGN §3.3 error normalization table: code -> process exit code.
ERROR_EXIT_CODES: dict[str, int] = {
    "auth_failed": EXIT_ERROR,
    "permission_denied": EXIT_ERROR,
    "not_found": EXIT_ERROR,
    "bad_query": EXIT_ERROR,
    "invalid_request": EXIT_ERROR,
    "rate_limited": EXIT_ERROR,
    "server_error": EXIT_ERROR,
    "network_error": EXIT_ERROR,
    "timeout": EXIT_ERROR,
    "config_missing": EXIT_ERROR,
    "unhealthy": EXIT_ERROR,
    "ambiguous_ref": EXIT_ERROR,
    "invalid_response": EXIT_ERROR,
    "not_implemented": EXIT_ERROR,
    "invalid_args": EXIT_USAGE,
    "write_blocked": EXIT_USAGE,
}


@dataclass
class MbcliError(Exception):
    """Every failure mode of mbcli, normalized to a code + message + detail.

    The stderr rendering is a single line of JSON:
    {"error":{"code":"...","message":"...","detail":{...}}}
    """

    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    @property
    def exit_code(self) -> int:
        return ERROR_EXIT_CODES.get(self.code, EXIT_ERROR)

    def to_stderr_json(self) -> str:
        payload = {
            "error": {
                "code": self.code,
                "message": " ".join(str(self.message).split()),
                "detail": self.detail or {},
            }
        }
        return json.dumps(payload, ensure_ascii=False)

    def emit(self, stream: Any | None = None) -> None:
        stream = sys.stderr if stream is None else stream
        stream.write(self.to_stderr_json() + "\n")
