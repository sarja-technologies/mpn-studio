"""XDG Base Directory locations for MPN Studio.

config  ~/.config/mpn-studio        settings
data    ~/.local/share/mpn-studio   projects, backups, trash
cache   ~/.cache/mpn-studio         disposable
state   ~/.local/state/mpn-studio   logs
Standard XDG_*_HOME overrides apply (a relative value is invalid per the specification and is ignored).
MPN_STUDIO_{CONFIG,DATA,CACHE,STATE}_DIR override a single location (tests, portable installs).
"""
from __future__ import annotations

import os
from pathlib import Path

APP = "mpn-studio"


def _dir(override: str, xdg: str, default: str) -> Path:
    v = os.environ.get(override)
    if v:
        return Path(v).expanduser()
    x = os.environ.get(xdg, "")
    base = Path(x) if x and os.path.isabs(x) else Path.home() / default
    return base / APP


def config_dir() -> Path: return _dir("MPN_STUDIO_CONFIG_DIR", "XDG_CONFIG_HOME", ".config")
def data_dir() -> Path: return _dir("MPN_STUDIO_DATA_DIR", "XDG_DATA_HOME", ".local/share")
def cache_dir() -> Path: return _dir("MPN_STUDIO_CACHE_DIR", "XDG_CACHE_HOME", ".cache")
def state_dir() -> Path: return _dir("MPN_STUDIO_STATE_DIR", "XDG_STATE_HOME", ".local/state")
def projects_dir() -> Path: return data_dir() / "projects"
def backups_dir() -> Path: return data_dir() / "backups"
def trash_dir() -> Path: return data_dir() / "trash"
def exports_dir() -> Path: return data_dir() / "exports"
def settings_file() -> Path: return config_dir() / "settings.json"


def ensure() -> None:
    for d in (config_dir(), data_dir(), cache_dir(), state_dir(), projects_dir(), backups_dir()):
        d.mkdir(parents=True, exist_ok=True)
