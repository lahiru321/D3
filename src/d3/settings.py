"""Choices made from the tray (e.g. the microphone), kept in data/settings.json.

config.toml is for hand-edited settings; this file is written by D3 itself.
"""

import json
from pathlib import Path
from typing import Any


class Settings:
    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
