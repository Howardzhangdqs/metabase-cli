"""Shared fixtures: isolated env, respx mock helper, and a `cli` runner that goes
through mbcli.cli:main so exit codes / stderr JSON are asserted end-to-end."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import respx

from mbcli.cli import main

MB_ENV_KEYS = (
    "MB_URL",
    "MB_USERNAME",
    "MB_PASSWORD",
    "MB_API_KEY",
    "MB_TIMEOUT",
    "MB_SESSION_CACHE",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """No MB_* leakage between tests; cwd is a tmp dir (no stray .env / writes)."""
    for key in MB_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # never touch the developer's real ~/.cache/mbcli/session.json
    monkeypatch.setenv("MB_SESSION_CACHE", str(tmp_path / "session.json"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@dataclass
class MbcliResult:
    exit_code: int
    stdout: str
    stderr: str
    exception: BaseException | None = field(default=None)

    def assert_ok(self, code: int = 0) -> MbcliResult:
        assert self.exit_code == code, (
            f"expected exit {code}, got {self.exit_code}\n"
            f"stdout: {self.stdout!r}\nstderr: {self.stderr!r}\n"
            f"exception: {self.exception!r}"
        )
        return self

    def json(self) -> Any:
        return json.loads(self.stdout)

    def error(self) -> dict[str, Any]:
        lines = [line for line in self.stderr.strip().splitlines() if line.strip()]
        assert lines, f"no stderr output; stdout={self.stdout!r}"
        parsed = json.loads(lines[-1])
        assert set(parsed) == {"error"}, f"stderr JSON must be a single error object, got {parsed}"
        return parsed["error"]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Any:
    """Invoke the real entry point with an argv list; capture stdout/stderr."""

    def invoke(*args: object) -> MbcliResult:
        exit_code = 0
        exception: BaseException | None = None
        try:
            main([str(arg) for arg in args])
        except SystemExit as exc:
            exit_code = int(exc.code or 0)
        except BaseException as exc:  # noqa: BLE001 - surfaced on the result
            exception = exc
            exit_code = 1
        captured = capsys.readouterr()
        return MbcliResult(exit_code, captured.out, captured.err, exception)

    return invoke


@pytest.fixture
def mock_api():
    with respx.mock(assert_all_called=False) as mock:
        yield mock


@pytest.fixture
def make_config(tmp_path: Path):
    from mbcli.config import Config

    def _make(**overrides: Any) -> Config:
        params: dict[str, Any] = {
            "url": "http://test.local",
            "session_cache": str(tmp_path / "session.json"),
        }
        params.update(overrides)
        return Config(**params)

    return _make
