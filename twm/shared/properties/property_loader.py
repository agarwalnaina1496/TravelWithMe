import configparser
import json
import os
from pathlib import Path
from typing import Any, List


PROPERTIES_DIR = Path(__file__).resolve().parent
ENVIRONMENT = os.getenv("ENVIRONMENT", "prod")
CONFIG = configparser.ConfigParser()
CONFIG.optionxform = str


def _read_properties(path: Path) -> None:
    if not path.exists():
        return
    try:
        CONFIG.read(path)
    except configparser.MissingSectionHeaderError:
        return


_read_properties(PROPERTIES_DIR / "properties.ini")
_read_properties(PROPERTIES_DIR / f"properties-{ENVIRONMENT}.ini")


def _env_key(key: str) -> str:
    return key.replace(".", "_").upper()


def _get_raw_property(key: str, group: str = "APP") -> str:
    return os.getenv(_env_key(key)) or CONFIG.get(group, key)


def get_string_property(key: str, group: str = "APP") -> str:
    return _get_raw_property(key, group)


def get_environment() -> str:
    return ENVIRONMENT


def get_string_property_with_default(key: str, default_value: str, group: str = "APP") -> str:
    env_value = os.getenv(_env_key(key))
    if env_value is not None:
        return env_value

    try:
        return CONFIG.get(group, key)
    except (configparser.NoOptionError, configparser.NoSectionError):
        return default_value


def get_list_property(key: str, group: str = "APP") -> List[Any]:
    return json.loads(get_string_property(key, group))


def get_int_property_with_default(key: str, default_value: int, group: str = "APP") -> int:
    return int(get_string_property_with_default(key, str(default_value), group))
