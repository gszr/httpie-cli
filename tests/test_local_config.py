import json
from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from httpie.config import (
    ENV_HTTPIE_LOCAL_CONFIG, LOCAL_CONFIG_FILENAME, LocalConfig,
    get_local_config_path, load_local_config,
)
from .utils import MockEnvironment, http

URL = 'http://example.com/path'


@pytest.fixture
def local_config(monkeypatch: MonkeyPatch, tmp_path: Path):
    """Yield a callable that writes a local config and points HTTPIE_LOCAL_CONFIG at it."""
    path = tmp_path / LOCAL_CONFIG_FILENAME

    def write(data):
        if isinstance(data, str):
            path.write_text(data)
        else:
            path.write_text(json.dumps(data))
        monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(path))
        return path

    monkeypatch.delenv(ENV_HTTPIE_LOCAL_CONFIG, raising=False)
    yield write


# --- Path resolution -------------------------------------------------------

def test_local_config_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / 'somewhere/.httpie'
    monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(custom))
    assert get_local_config_path() == custom


def test_local_config_path_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.delenv(ENV_HTTPIE_LOCAL_CONFIG, raising=False)
    monkeypatch.chdir(tmp_path)
    assert get_local_config_path() == tmp_path / LOCAL_CONFIG_FILENAME


# --- Loader ----------------------------------------------------------------

def test_load_returns_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(tmp_path / 'missing'))
    assert load_local_config() is None


def test_load_returns_typed_object(monkeypatch, tmp_path):
    path = tmp_path / LOCAL_CONFIG_FILENAME
    path.write_text(json.dumps({
        'default_options': ['--form'],
        'headers': {'X-A': '1'},
        'query': {'q': 'v'},
        'mystery': 'ignored',
    }))
    monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(path))
    cfg = load_local_config()
    assert isinstance(cfg, LocalConfig)
    assert cfg.default_options == ['--form']
    assert cfg.headers == {'X-A': '1'}
    assert cfg.query == {'q': 'v'}
    assert 'mystery' not in cfg
    assert not cfg.is_empty()


def test_load_empty_object_is_empty(monkeypatch, tmp_path):
    path = tmp_path / LOCAL_CONFIG_FILENAME
    path.write_text('{}')
    monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(path))
    cfg = load_local_config()
    assert cfg.is_empty()


# --- Integration via --offline --------------------------------------------

def test_no_local_config_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_HTTPIE_LOCAL_CONFIG, str(tmp_path / 'missing'))
    r = http('--offline', URL, env=MockEnvironment())
    assert 'loaded local config' not in r.stderr
    assert 'X-' not in r  # no surprise headers


def test_default_options_from_local(local_config):
    local_config({'default_options': ['--form']})
    r = http('--offline', 'POST', URL, 'foo=bar', env=MockEnvironment())
    assert 'Content-Type: application/x-www-form-urlencoded' in r
    assert 'foo=bar' in r


def test_cli_overrides_local_default_options(local_config):
    local_config({'default_options': ['--form']})
    r = http('--offline', '--json', 'POST', URL, 'foo=bar', env=MockEnvironment())
    assert 'Content-Type: application/json' in r


def test_local_overrides_user_default_options(local_config):
    env = MockEnvironment()
    env.config['default_options'] = ['--form']
    env.config.save()
    local_config({'default_options': ['--json']})
    r = http('--offline', 'POST', URL, 'foo=bar', env=env)
    assert 'Content-Type: application/json' in r


def test_header_from_local(local_config):
    local_config({'headers': {'X-Custom': 'from-local'}})
    r = http('--offline', URL, env=MockEnvironment())
    assert 'X-Custom: from-local' in r


def test_cli_overrides_local_header(local_config):
    local_config({'headers': {'X-Custom': 'from-local'}})
    r = http('--offline', URL, 'X-Custom:from-cli', env=MockEnvironment())
    assert 'X-Custom: from-cli' in r
    assert 'from-local' not in r


def test_local_header_case_insensitive_against_cli(local_config):
    local_config({'headers': {'X-Custom': 'from-local'}})
    r = http('--offline', URL, 'x-custom:from-cli', env=MockEnvironment())
    assert 'from-cli' in r
    assert 'from-local' not in r


def test_query_from_local(local_config):
    local_config({'query': {'api_version': '2'}})
    r = http('--offline', URL, env=MockEnvironment())
    assert 'GET /path?api_version=2' in r


def test_cli_overrides_local_query(local_config):
    local_config({'query': {'api_version': '2'}})
    r = http('--offline', URL, 'api_version==3', env=MockEnvironment())
    assert 'api_version=3' in r
    assert 'api_version=2' not in r


def test_stderr_notice_when_loaded(local_config):
    local_config({'headers': {'X-Custom': 'x'}})
    r = http('--offline', URL, env=MockEnvironment())
    assert 'loaded local config' in r.stderr
    assert '1 headers' in r.stderr


def test_no_stderr_notice_when_empty(local_config):
    local_config({})
    r = http('--offline', URL, env=MockEnvironment())
    assert 'loaded local config' not in r.stderr


def test_invalid_json_warns_and_continues(local_config):
    local_config('{not valid json')
    r = http('--offline', URL, env=MockEnvironment())
    assert 'warning' in r.stderr
    assert 'invalid local config file' in r.stderr


def test_non_object_top_level_warns(local_config):
    local_config([1, 2, 3])
    r = http('--offline', URL, env=MockEnvironment())
    assert 'warning' in r.stderr


def test_unknown_keys_ignored(local_config):
    local_config({'headers': {'X-Custom': 'x'}, 'mystery': 'value'})
    r = http('--offline', URL, env=MockEnvironment())
    assert 'X-Custom: x' in r
