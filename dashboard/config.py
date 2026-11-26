"""
Dashboard settings, persisted as JSON next to this package.

The dashboard never rewrites help_crack.py; it only stores the knobs used when
spawning it (interpreter, script path, working directory, cracker options).
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
BASE_DIR = PKG_DIR.parent

SETTINGS_FILE = PKG_DIR / "settings.json"
KEYS_FILE = PKG_DIR / "keys.jsonl"
SESSION_LOG = PKG_DIR / "session.log"
GEO_FILE = PKG_DIR / "geo.json"
WORLD_FILE = PKG_DIR / "world.json"
LAND_FILE = PKG_DIR / "land.json"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 8666,
    # directory help_crack.py runs in; artifact files (.res/.hash/.key) live here
    "workdir": str(BASE_DIR),
    "python": sys.executable or "python",
    "script": "help_crack.py",
    # refuse to crack on the CPU: requires a visible GPU backend
    "gpu_only": True,
    # let the client replace itself when the server announces a newer version
    "client_update": False,
    # read-only lookups on the public wpa-sec pages (country flags, account founds)
    "remote_enabled": True,
    # optional wpa-sec account key (32 hex); WPASEC_KEY wins over this
    "site_key": "",
    # mirrors help_crack.py command line options
    "coptions": "",
    "potfile": "",
    "additional": "",
    # dashboard behaviour
    "log_capacity": 5000,
    "poll_interval": 1.0,
    # artifact file names, must match help_crack.py conf
    "res_file": "help_crack.res",
    "hash_file": "help_crack.hash",
    "key_file": "help_crack.key",
}

_INT_FIELDS = {"port": (1, 65535), "log_capacity": (100, 200000)}
_BOOL_FIELDS = {"gpu_only", "client_update", "remote_enabled"}
_TRUE_WORDS = ("1", "true", "yes", "on", "y")
_FALSE_WORDS = ("", "0", "false", "no", "off", "n")


class Settings():
    """Thread safe, dict-like settings holder with JSON persistence"""

    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_FILE
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        """merge persisted settings over the defaults"""
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8") as fd:
                    stored = json.load(fd)
            except (OSError, ValueError):
                return
            if not isinstance(stored, dict):
                return
            for key, value in stored.items():
                if key in DEFAULTS and _valid(key, value):
                    self._data[key] = _coerce(key, value)

    def save(self):
        """write settings to disk, creating the parent directory if needed"""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fd:
                json.dump(self._data, fd, indent=2, sort_keys=True)
            os.replace(tmp, self.path)

    def update(self, values):
        """apply a partial update, ignoring unknown or invalid keys"""
        applied = {}
        with self._lock:
            for key, value in (values or {}).items():
                if key in DEFAULTS and _valid(key, value):
                    self._data[key] = _coerce(key, value)
                    applied[key] = self._data[key]
        return applied

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def __getitem__(self, key):
        return self.get(key)

    def as_dict(self):
        with self._lock:
            return dict(self._data)

    def path_for(self, key):
        """absolute path of an artifact file inside the working directory"""
        return str(Path(self.get("workdir")).expanduser() / self.get(key))


def _coerce(key, value):
    if key in _INT_FIELDS:
        return int(value)
    if key in _BOOL_FIELDS:
        return _as_bool(value)
    if key == "poll_interval":
        return float(value)
    return value


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    return False


def _valid(key, value):
    """reject values that would break the runner"""
    if key in _INT_FIELDS:
        low, high = _INT_FIELDS[key]
        try:
            return low <= int(value) <= high
        except (TypeError, ValueError):
            return False
    if key in _BOOL_FIELDS:
        if isinstance(value, (bool, int)):
            return True
        return str(value).strip().lower() in _TRUE_WORDS + _FALSE_WORDS
    if key == "site_key":
        text = str(value).strip()
        return text == "" or bool(re.fullmatch(r"[0-9a-fA-F]{32}", text))
    if key == "poll_interval":
        try:
            return 0.2 <= float(value) <= 30.0
        except (TypeError, ValueError):
            return False
    return isinstance(value, str) and "\x00" not in value
