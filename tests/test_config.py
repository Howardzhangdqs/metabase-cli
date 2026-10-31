"""Config resolution (DESIGN §3.1): precedence, lazy credentials, .env parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from mbcli.config import Config, default_session_cache, load_config, parse_dotenv, parse_toml
from mbcli.errors import MbcliError

# -- precedence ---------------------------------------------------------------


def test_cli_overrides_env(make_dotenv, tmp_path):
    (toml := tmp_path / "config.toml").write_text("[metabase]\nurl = 'http://toml'\n")
    config = load_config(
        {"url": "http://cli", "timeout": 30},
        env={"MB_URL": "http://env", "MB_TIMEOUT": "45"},
        dotenv_path=make_dotenv({"MB_URL": "http://dotenv"}),
        toml_path=toml,
    )
    assert config.url == "http://cli"
    assert config.timeout == 30


def test_env_beats_dotenv(make_dotenv, tmp_path):
    config = load_config(
        None,
        env={"MB_URL": "http://env"},
        dotenv_path=make_dotenv({"MB_URL": "http://dotenv"}),
        toml_path=tmp_path / "none.toml",
    )
    assert config.url == "http://env"


def test_dotenv_beats_toml(make_dotenv, tmp_path):
    (toml := tmp_path / "config.toml").write_text("[metabase]\nurl = 'http://toml'\ntimeout = 90\n")
    config = load_config(
        None,
        env={},
        dotenv_path=make_dotenv({"MB_URL": "http://dotenv"}),
        toml_path=toml,
    )
    assert config.url == "http://dotenv"
    assert config.timeout == 90  # toml still wins for unset-elsewhere keys


def test_toml_beats_defaults(tmp_path):
    (toml := tmp_path / "config.toml").write_text(
        "[metabase]\nurl = 'http://toml'\ntimeout = 15\nsession_cache = '/tmp/x.json'\n"
    )
    config = load_config(None, env={}, dotenv_path=tmp_path / "none.env", toml_path=toml)
    assert config.url == "http://toml"
    assert config.timeout == 15
    assert config.session_cache == "/tmp/x.json"


def test_defaults_when_nothing_set(tmp_path):
    config = load_config(
        {"url": "http://only.example"},
        env={},
        dotenv_path=tmp_path / "none.env",
        toml_path=tmp_path / "none.toml",
    )
    assert config.timeout == 60
    assert config.session_cache == default_session_cache()
    assert config.api_key is None


# -- url handling ----------------------------------------------------------------


def test_missing_url_raises_config_missing(tmp_path):
    with pytest.raises(MbcliError) as excinfo:
        load_config(None, env={}, dotenv_path=tmp_path / "n.env", toml_path=tmp_path / "n.toml")
    assert excinfo.value.code == "config_missing"
    assert excinfo.value.exit_code == 1


def test_empty_env_url_treated_as_unset(tmp_path):
    with pytest.raises(MbcliError) as excinfo:
        load_config(
            None,
            env={"MB_URL": "   "},
            dotenv_path=tmp_path / "n.env",
            toml_path=tmp_path / "n.toml",
        )
    assert excinfo.value.code == "config_missing"


def test_trailing_slash_stripped_from_every_source(tmp_path):
    for source in ({"url": "http://x//"},):
        config = load_config(
            source, env={}, dotenv_path=tmp_path / "n.env", toml_path=tmp_path / "n.toml"
        )
        assert config.url == "http://x"


def test_env_url_trailing_slash_stripped(tmp_path):
    config = load_config(
        None,
        env={"MB_URL": "http://x/"},
        dotenv_path=tmp_path / "n.env",
        toml_path=tmp_path / "n.toml",
    )
    assert config.url == "http://x"


# -- timeout ------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "1.5"])
def test_invalid_timeout_raises_config_missing(bad, tmp_path):
    with pytest.raises(MbcliError) as excinfo:
        load_config(
            None,
            env={"MB_URL": "http://x", "MB_TIMEOUT": bad},
            dotenv_path=tmp_path / "n.env",
            toml_path=tmp_path / "n.toml",
        )
    assert excinfo.value.code == "config_missing"


def test_timeout_from_env_integer(tmp_path):
    config = load_config(
        None,
        env={"MB_URL": "http://x", "MB_TIMEOUT": "12"},
        dotenv_path=tmp_path / "n.env",
        toml_path=tmp_path / "n.toml",
    )
    assert config.timeout == 12


# -- lazy credentials ----------------------------------------------------------------


def test_load_succeeds_without_credentials(tmp_path):
    """No api key / username / password: must NOT raise at load time."""
    config = load_config(
        None,
        env={"MB_URL": "http://x"},
        dotenv_path=tmp_path / "n.env",
        toml_path=tmp_path / "n.toml",
    )
    assert config.username is None


def test_require_credentials_missing_everything():
    with pytest.raises(MbcliError) as excinfo:
        Config(url="http://x").require_credentials()
    assert excinfo.value.code == "config_missing"
    assert "MB_USERNAME" in excinfo.value.message
    assert "MB_PASSWORD" in excinfo.value.message


def test_require_credentials_username_only_fails():
    with pytest.raises(MbcliError) as excinfo:
        Config(url="http://x", username="u").require_credentials()
    assert excinfo.value.code == "config_missing"


def test_require_credentials_pair_ok():
    Config(url="http://x", username="u", password="p").require_credentials()


def test_require_credentials_api_key_short_circuits():
    Config(url="http://x", api_key="k").require_credentials()


# -- .env parser -----------------------------------------------------------------------


@pytest.fixture
def make_dotenv(tmp_path: Path):
    def _make(values: dict[str, str] | str) -> Path:
        path = tmp_path / ".env"
        if isinstance(values, str):
            path.write_text(values, encoding="utf-8")
        else:
            path.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        return path

    return _make


def test_dotenv_quotes_stripped(make_dotenv):
    path = make_dotenv("MB_URL='http://quoted'\nMB_USERNAME=\"quoted-user\"\n")
    parsed = parse_dotenv(path)
    assert parsed == {"MB_URL": "http://quoted", "MB_USERNAME": "quoted-user"}


def test_dotenv_comments_and_blank_lines(make_dotenv):
    path = make_dotenv(
        "# top comment\n\nMB_URL=http://plain  \n"
        "MB_USERNAME=u # trailing comment\n"
        "# MB_PASSWORD=not-loaded-because-comment\n"
    )
    parsed = parse_dotenv(path)
    assert parsed == {"MB_URL": "http://plain", "MB_USERNAME": "u"}


def test_dotenv_hash_inside_quotes_preserved(make_dotenv):
    path = make_dotenv("MB_PASSWORD='pa#ss'  # real comment\n")
    parsed = parse_dotenv(path)
    assert parsed == {"MB_PASSWORD": "pa#ss"}


def test_dotenv_unknown_keys_ignored(make_dotenv):
    path = make_dotenv("FOO=1\nMB_URL=http://x\nDEBUG=yes\n")
    parsed = parse_dotenv(path)
    assert parsed == {"MB_URL": "http://x"}


def test_dotenv_empty_values_ignored(make_dotenv):
    path = make_dotenv("MB_API_KEY=\nMB_URL=http://x\n")
    parsed = parse_dotenv(path)
    assert parsed == {"MB_URL": "http://x"}


def test_dotenv_missing_file_returns_empty(tmp_path):
    assert parse_dotenv(tmp_path / "missing.env") == {}


# -- toml parser --------------------------------------------------------------------------


def test_toml_only_metabase_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[other]\nurl = 'http://ignore'\n[metabase]\nurl = 'http://real'\nusername = 'u'\n"
    )
    parsed = parse_toml(path)
    assert parsed == {"url": "http://real", "username": "u"}


def test_toml_unknown_keys_dropped(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[metabase]\nurl = 'http://x'\nbogus_key = 1\n")
    assert parse_toml(path) == {"url": "http://x"}


def test_toml_invalid_file_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is ][ not toml")
    assert parse_toml(path) == {}
