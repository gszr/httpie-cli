import json
import os
from pathlib import Path
from typing import Any, Dict, Union

from . import __version__
from .compat import is_windows
from .encoding import UTF8


ENV_XDG_CONFIG_HOME = 'XDG_CONFIG_HOME'
ENV_HTTPIE_CONFIG_DIR = 'HTTPIE_CONFIG_DIR'
ENV_HTTPIE_LOCAL_CONFIG = 'HTTPIE_LOCAL_CONFIG'
DEFAULT_CONFIG_DIRNAME = 'httpie'
DEFAULT_RELATIVE_XDG_CONFIG_HOME = Path('.config')
DEFAULT_RELATIVE_LEGACY_CONFIG_DIR = Path('.httpie')
DEFAULT_WINDOWS_CONFIG_DIR = Path(
    os.path.expandvars('%APPDATA%')) / DEFAULT_CONFIG_DIRNAME
LOCAL_CONFIG_FILENAME = '.httpie'
LOCAL_CONFIG_KEYS = ('default_options', 'headers', 'query')


def get_default_config_dir() -> Path:
    """
    Return the path to the httpie configuration directory.

    This directory isn't guaranteed to exist, and nor are any of its
    ancestors (only the legacy ~/.httpie, if returned, is guaranteed to exist).

    XDG Base Directory Specification support:

        <https://wiki.archlinux.org/index.php/XDG_Base_Directory>

        $XDG_CONFIG_HOME is supported; $XDG_CONFIG_DIRS is not

    """
    # 1. explicitly set through env
    env_config_dir = os.environ.get(ENV_HTTPIE_CONFIG_DIR)
    if env_config_dir:
        return Path(env_config_dir)

    # 2. Windows
    if is_windows:
        return DEFAULT_WINDOWS_CONFIG_DIR

    home_dir = Path.home()

    # 3. legacy ~/.httpie
    legacy_config_dir = home_dir / DEFAULT_RELATIVE_LEGACY_CONFIG_DIR
    if legacy_config_dir.exists():
        return legacy_config_dir

    # 4. XDG
    xdg_config_home_dir = os.environ.get(
        ENV_XDG_CONFIG_HOME,  # 4.1. explicit
        home_dir / DEFAULT_RELATIVE_XDG_CONFIG_HOME  # 4.2. default
    )
    return Path(xdg_config_home_dir) / DEFAULT_CONFIG_DIRNAME


DEFAULT_CONFIG_DIR = get_default_config_dir()


class ConfigFileError(Exception):
    pass


def read_raw_config(config_type: str, path: Path) -> Dict[str, Any]:
    try:
        with path.open(encoding=UTF8) as f:
            try:
                return json.load(f)
            except ValueError as e:
                raise ConfigFileError(
                    f'invalid {config_type} file: {e} [{path}]'
                )
    except FileNotFoundError:
        pass
    except OSError as e:
        raise ConfigFileError(f'cannot read {config_type} file: {e}')


class BaseConfigDict(dict):
    name = None
    helpurl = None
    about = None

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def ensure_directory(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    def is_new(self) -> bool:
        return not self.path.exists()

    def pre_process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for processing the incoming config data."""
        return data

    def post_process_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Hook for processing the outgoing config data."""
        return data

    def load(self):
        config_type = type(self).__name__.lower()
        data = read_raw_config(config_type, self.path)
        if data is not None:
            data = self.pre_process_data(data)
            self.update(data)

    def save(self, *, bump_version: bool = False):
        self.setdefault('__meta__', {})
        if bump_version or 'httpie' not in self['__meta__']:
            self['__meta__']['httpie'] = __version__
        if self.helpurl:
            self['__meta__']['help'] = self.helpurl

        if self.about:
            self['__meta__']['about'] = self.about

        self.ensure_directory()

        json_string = json.dumps(
            obj=self.post_process_data(self),
            indent=4,
            sort_keys=True,
            ensure_ascii=True,
        )
        self.path.write_text(json_string + '\n', encoding=UTF8)

    @property
    def version(self):
        return self.get(
            '__meta__', {}
        ).get('httpie', __version__)


class Config(BaseConfigDict):
    FILENAME = 'config.json'
    DEFAULTS = {
        'default_options': []
    }

    def __init__(self, directory: Union[str, Path] = DEFAULT_CONFIG_DIR):
        self.directory = Path(directory)
        super().__init__(path=self.directory / self.FILENAME)
        self.update(self.DEFAULTS)

    @property
    def default_options(self) -> list:
        return self['default_options']

    def _configured_path(self, config_option: str, default: str) -> None:
        return Path(
            self.get(config_option, self.directory / default)
        ).expanduser().resolve()

    @property
    def plugins_dir(self) -> Path:
        return self._configured_path('plugins_dir', 'plugins')

    @property
    def version_info_file(self) -> Path:
        return self._configured_path('version_info_file', 'version_info.json')

    @property
    def developer_mode(self) -> bool:
        """This is a special setting for the development environment. It is
        different from the --debug mode in the terms that it might change
        the behavior for certain parameters (e.g updater system) that
        we usually ignore."""

        return self.get('developer_mode')


class LocalConfig(dict):
    """A per-CWD `.httpie` config layered on top of the user config.

    Recognised keys: `default_options` (list of CLI args),
    `headers` (object) and `query` (object). Unknown keys are ignored.
    """

    def __init__(self, path: Path, data: Dict[str, Any]):
        super().__init__()
        self.path = path
        for key in LOCAL_CONFIG_KEYS:
            if key in data:
                self[key] = data[key]

    @property
    def default_options(self) -> list:
        value = self.get('default_options', [])
        return value if isinstance(value, list) else []

    @property
    def headers(self) -> Dict[str, str]:
        value = self.get('headers', {})
        return value if isinstance(value, dict) else {}

    @property
    def query(self) -> Dict[str, str]:
        value = self.get('query', {})
        return value if isinstance(value, dict) else {}

    def is_empty(self) -> bool:
        return not (self.default_options or self.headers or self.query)

    def apply_to_parsed_args(self, args) -> None:
        """Fill in headers/query from local config, never overriding CLI values."""
        if self.headers and hasattr(args, 'headers'):
            for key, value in self.headers.items():
                if key not in args.headers:
                    args.headers.add(key, value)
        if self.query and hasattr(args, 'params'):
            for key, value in self.query.items():
                if key not in args.params:
                    args.params[key] = value


def get_local_config_path() -> Path:
    """Resolve the path the local config would live at (may not exist)."""
    override = os.environ.get(ENV_HTTPIE_LOCAL_CONFIG)
    if override:
        return Path(override)
    return Path.cwd() / LOCAL_CONFIG_FILENAME


def load_local_config() -> Union['LocalConfig', None]:
    """Return the local config from CWD, or None if absent/unreadable.

    Raises ConfigFileError on invalid JSON so callers can surface it.
    """
    try:
        path = get_local_config_path()
    except (OSError, FileNotFoundError):
        # CWD was deleted out from under us.
        return None
    data = read_raw_config('local config', path)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ConfigFileError(
            f'invalid local config file: top-level value must be an object [{path}]'
        )
    return LocalConfig(path=path, data=data)
