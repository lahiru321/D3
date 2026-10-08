"""Settings: config.default.toml, overridden by config.toml if present."""

import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        out[key] = _merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def load_config(root: Path = ROOT) -> dict[str, Any]:
    with open(root / "config.default.toml", "rb") as f:
        cfg = tomllib.load(f)
    user = root / "config.toml"
    if user.exists():
        with open(user, "rb") as f:
            cfg = _merge(cfg, tomllib.load(f))
    return cfg


def resolve(path: str, root: Path = ROOT) -> Path:
    p = Path(path)
    return p if p.is_absolute() else root / p
