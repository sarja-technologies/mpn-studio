"""User settings: ~/.config/mpn-studio/settings.json. Small, human-editable, written atomically."""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

from . import paths

VERSION = 1
DEFAULTS: dict = {
    "version": VERSION,
    "last_project": None,
    # Large assets stay wherever the user keeps them. null = not configured. Nothing is copied automatically.
    "storage": {"models_dir": None, "checkpoints_dir": None, "datasets_dir": None},
    "compute": {
        "mode": "local",                      # "local" (default) or "remote"
        "python": None,                       # optional interpreter that has torch + the Yaob runtime (local inference/training)
        "remote": {"ssh_host": "", "ssh_config": "", "api_port": 8840, "runtime_port": 8841},
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


def load() -> dict:
    try:
        raw = json.loads(paths.settings_file().read_text())
    except (OSError, ValueError):
        raw = {}
    return _merge(DEFAULTS, raw)


def save(s: dict) -> dict:
    s = _merge(DEFAULTS, s)
    s["version"] = VERSION
    paths.config_dir().mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=paths.config_dir(), prefix=".settings-", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    os.replace(tmp, paths.settings_file())
    return s


def update(patch: dict) -> dict:
    cur = load()
    s = _merge(cur, patch)
    for k in ("models_dir", "checkpoints_dir", "datasets_dir"):
        v = s["storage"].get(k)
        if v:
            if not os.path.isabs(os.path.expanduser(v)):
                raise ValueError(f"{k} must be an absolute path")
            s["storage"][k] = str(Path(v).expanduser())
    if s["compute"]["mode"] not in ("local", "remote"):
        raise ValueError("compute.mode must be 'local' or 'remote'")
    r = s["compute"]["remote"]
    for k in ("api_port", "runtime_port"):
        r[k] = int(r[k])
        if not 1 <= r[k] <= 65535:
            raise ValueError(f"{k} out of range")
    return save(s)
