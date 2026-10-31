"""Configuration resolution (DESIGN §3.1).

Precedence: CLI explicit flags > MB_* environment variables > ./.env >
~/.config/mbcli/config.toml > defaults. URL is validated eagerly at load time;
credentials are validated lazily (first authenticated request) so that
`health` runs without any credentials.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from mbcli.errors import MbcliError

try:  # Python >= 3.11
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 fallback
    tomllib = None  # type: ignore[assignment]

DEFAULT_TIMEOUT = 60

# --read-only hint raised by commands when --db is omitted and no default exists
# (DESIGN §3.1 `default_db`; the message must hint all three fixes)
NO_DB_MESSAGE = (
    "no database selected: pass --db, run `mbcli connect --default-db`, or set MB_DEFAULT_DB"
)

# .env whitelist (DESIGN §2: no python-dotenv, ~20 line built-in parser).
ENV_KEY_MAP: dict[str, str] = {
    "MB_URL": "url",
    "MB_USERNAME": "username",
    "MB_PASSWORD": "password",
    "MB_API_KEY": "api_key",
}
DOTENV_WHITELIST = frozenset(ENV_KEY_MAP)

TOML_SECTION = "metabase"
TOML_KEYS = frozenset(
    {"url", "api_key", "username", "password", "timeout", "session_cache", "default_db"}
)


def default_session_cache() -> str:
    return str(Path.home() / ".cache" / "mbcli" / "session.json")


def default_toml_path() -> Path:
    return Path.home() / ".config" / "mbcli" / "config.toml"


@dataclass
class Config:
    """Resolved configuration. `url` is always slash-stripped and non-empty
    (except for the internal require_url=False path used by `mbcli connect`,
    which resolves the remaining keys before prompting for the URL)."""

    url: str
    api_key: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: int = DEFAULT_TIMEOUT
    session_cache: str = field(default_factory=default_session_cache)
    # --db fallback chain (DESIGN §3.1): MB_DEFAULT_DB env > config.toml default_db
    default_db: str | None = None
    # where `connect` persists the [metabase] section (MB_CONFIG_FILE redirects)
    config_file: str = field(default_factory=lambda: str(default_toml_path()))

    def httpx_timeout(self) -> httpx.Timeout:
        """DESIGN §3.1: N is a per-request read timeout, not a total duration."""
        return httpx.Timeout(
            connect=5.0,
            read=float(self.timeout),
            write=float(self.timeout),
            pool=5.0,
        )

    def require_credentials(self) -> None:
        """Lazy credential check: no api key and no full username/password pair."""
        if self.api_key:
            return
        missing = [
            name
            for name, value in (("MB_USERNAME", self.username), ("MB_PASSWORD", self.password))
            if not value
        ]
        if missing:
            raise MbcliError(
                "config_missing",
                "missing credentials: set MB_API_KEY or " + " and ".join(missing),
            )


def effective_db_ref(explicit: str | None, config: Config) -> str:
    """Resolve the database reference for a command: explicit `--db` wins, then
    the `default_db` fallback (MB_DEFAULT_DB env > config.toml, already merged
    into `config`). All three missing -> invalid_args (exit 2) with a message
    hinting every fix (DESIGN §3.1, ADR #18)."""
    if explicit:
        return explicit
    if config.default_db:
        return str(config.default_db)
    raise MbcliError("invalid_args", NO_DB_MESSAGE)


def _clean(value: str | None) -> str | None:
    """Treat empty / whitespace-only strings as unset."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def parse_dotenv(path: str | Path) -> dict[str, str]:
    """Parse a .env file: whitelist keys only, '#' comments, quote stripping."""
    file = Path(path).expanduser()
    if not file.is_file():
        return {}
    values: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip()
        if key not in DOTENV_WHITELIST:
            continue
        value = raw.strip()
        if value[:1] in ("'", '"'):
            # quoted: keep everything (incl. '#') up to the matching close quote
            end = value.find(value[0], 1)
            if end != -1:
                value = value[1:end]
        else:
            # unquoted: strip a trailing comment
            comment_at = value.find(" #")
            if comment_at != -1:
                value = value[:comment_at].rstrip()
        if value:
            values[key] = value
    return values


def parse_toml(path: str | Path) -> dict[str, Any]:
    """Parse ~/.config/mbcli/config.toml; only the [metabase] section is honored."""
    file = Path(path).expanduser()
    if tomllib is None or not file.is_file():
        return {}
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}
    section = data.get(TOML_SECTION)
    if not isinstance(section, dict):
        return {}
    return {key: value for key, value in section.items() if key in TOML_KEYS}


def load_config(
    cli: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    toml_path: str | Path | None = None,
    require_url: bool = True,
) -> Config:
    """Resolve configuration from all sources. Raises config_missing without a
    URL (unless require_url=False, the lenient path used by `mbcli connect`,
    which prompts for the URL itself)."""
    cli_over: dict[str, Any] = dict(cli or {})
    env_map: Mapping[str, str] = os.environ if env is None else env
    dotenv = parse_dotenv(Path(".env") if dotenv_path is None else dotenv_path)
    # where to read the [metabase] section from: explicit test injection >
    # MB_CONFIG_FILE env > default path (DESIGN §3.1 config_file row)
    config_file = (
        str(toml_path)
        if toml_path is not None
        else (cli_over.get("config_file") or _clean(env_map.get("MB_CONFIG_FILE")))
    )
    if not config_file:
        config_file = str(default_toml_path())
    toml_cfg = parse_toml(config_file)

    def pick(key: str, env_key: str, default: Any) -> Any:
        if cli_over.get(key) is not None:
            return cli_over[key]
        for source in (env_map, dotenv):
            value = _clean(source.get(env_key))
            if value is not None:
                return value
        if toml_cfg.get(key) is not None:
            return toml_cfg[key]
        return default

    url = _clean(str(pick("url", "MB_URL", "") or ""))
    if not url and require_url:
        raise MbcliError(
            "config_missing",
            "MB_URL is not set: use --url, the MB_URL environment variable, .env,"
            " or run `mbcli connect` once",
        )

    timeout_raw = pick("timeout", "MB_TIMEOUT", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError) as exc:
        raise MbcliError(
            "config_missing", f"invalid timeout value {timeout_raw!r}: must be an integer"
        ) from exc

    default_db_raw = pick("default_db", "MB_DEFAULT_DB", None)
    default_db = _clean(str(default_db_raw)) if default_db_raw is not None else None

    return Config(
        url=(url or "").rstrip("/"),
        api_key=_clean(str(pick("api_key", "MB_API_KEY", "") or "")),
        username=_clean(str(pick("username", "MB_USERNAME", "") or "")),
        password=_clean(str(pick("password", "MB_PASSWORD", "") or "")),
        timeout=timeout,
        session_cache=str(pick("session_cache", "MB_SESSION_CACHE", default_session_cache())),
        default_db=default_db,
        config_file=str(config_file),
    )


# -- [metabase] section persistence (connect/disconnect, ADR #18) ------------------
#
# Python 3.10 has no TOML writer in the stdlib (tomllib is read-only), so the
# flat string-only section is hand-written with proper basic-string quoting;
# reading goes through the existing tomllib-based parse_toml.


def _toml_quote(value: str) -> str:
    """Escape as a TOML basic string: backslash, double quote, control chars."""
    escaped = (
        value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    )
    escaped = "".join(char if char >= " " else f"\\u{ord(char):04X}" for char in escaped)
    return f'"{escaped}"'


def _atomic_write(path: Path, text: str) -> None:
    """Tempfile in the same directory -> chmod 0600 -> os.replace; dir 0700
    (same discipline as the session cache, DESIGN §3.2)."""
    directory = path.parent
    directory_existed = directory.is_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not directory_existed:
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass
    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_metabase_section(config_file: str | Path, values: Mapping[str, Any]) -> None:
    """Atomically persist exactly the given [metabase] keys (order preserved,
    None values skipped). Replaces any previous [metabase] section."""
    path = Path(config_file).expanduser()
    lines = ["[metabase]"]
    lines += [
        f"{key} = {_toml_quote(str(value))}" for key, value in values.items() if value is not None
    ]
    _atomic_write(path, "\n".join(lines) + "\n")


def remove_metabase_section(config_file: str | Path) -> bool:
    """Drop the [metabase] section; delete the file when nothing meaningful
    (non-comment, non-blank) remains. Returns True if a section was present."""
    path = Path(config_file).expanduser()
    if not path.is_file():
        return False
    kept: list[str] = []
    in_metabase = False
    saw_metabase = False
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_metabase = stripped == f"[{TOML_SECTION}]"
            saw_metabase = saw_metabase or in_metabase
            if not in_metabase:
                kept.append(line)
            continue
        if not in_metabase:
            kept.append(line)
    if not saw_metabase:
        return False
    meaningful = [line for line in kept if line.strip() and not line.strip().startswith("#")]
    if not meaningful:
        path.unlink()
    else:
        _atomic_write(path, "\n".join(kept).rstrip("\n") + "\n")
    return True
