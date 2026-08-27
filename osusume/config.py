from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "default.yaml"
USER_CONFIG_PATH = ROOT / "config" / "local.yaml"


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    with DEFAULT_CONFIG_PATH.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    local = path or USER_CONFIG_PATH
    if local.exists():
        with local.open(encoding="utf-8") as handle:
            config = _merge(config, yaml.safe_load(handle) or {})
    for key in ("cards", "drafts", "runs"):
        raw = Path(config["paths"][key])
        config["paths"][key] = raw if raw.is_absolute() else ROOT / raw
    return config


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    copy = deepcopy(config)
    copy["paths"] = {key: str(value) for key, value in copy["paths"].items()}
    return copy


def set_config_value(key: str, raw_value: str, path: Path | None = None) -> None:
    parts = key.split(".")
    if not parts or any(not part for part in parts):
        raise ValueError("config key must use dotted names, such as models.default_model")
    target_path = path or USER_CONFIG_PATH
    current: dict[str, Any] = {}
    if target_path.exists():
        with target_path.open(encoding="utf-8") as handle:
            current = yaml.safe_load(handle) or {}
    node = current
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"{part} is not a config section")
        node = child
    node[parts[-1]] = yaml.safe_load(raw_value)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(yaml.safe_dump(current, sort_keys=False), encoding="utf-8")
