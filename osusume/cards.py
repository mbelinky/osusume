from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


CORE_LOAD_BEARING = {"operational_status", "hours_at_arrival", "detour"}
EPHEMERAL_ALLOWED = {
    "category",
    "country",
    "languages",
    "places_types",
    "query_templates",
    "load_bearing_claims",
    "freshness_overrides",
    "event_shaped",
    "reviewed",
    "auto_written",
}


class CardValidationError(ValueError):
    pass


def validate_card(card: dict[str, Any], defaults: dict[str, int]) -> dict[str, Any]:
    required = {"category", "languages", "places_types", "query_templates", "load_bearing_claims"}
    missing = sorted(required - card.keys())
    if missing:
        raise CardValidationError(f"missing card fields: {', '.join(missing)}")
    reviewed = bool(card.get("reviewed"))
    if not reviewed:
        extra = sorted(set(card) - EPHEMERAL_ALLOWED)
        if extra:
            raise CardValidationError(f"ephemeral card cannot declare: {', '.join(extra)}")
        if card.get("sources"):
            raise CardValidationError("ephemeral card cannot declare registry sources")
    claims = set(card.get("load_bearing_claims", []))
    if not CORE_LOAD_BEARING.issubset(claims):
        missing_claims = sorted(CORE_LOAD_BEARING - claims)
        raise CardValidationError(f"card cannot drop core claims: {', '.join(missing_claims)}")
    for claim_type, days in (card.get("freshness_overrides") or {}).items():
        if claim_type not in defaults:
            raise CardValidationError(f"unknown freshness claim type: {claim_type}")
        if not isinstance(days, int) or days < 0 or days > defaults[claim_type]:
            raise CardValidationError(f"freshness override for {claim_type} must tighten {defaults[claim_type]} days")
    for country, sources in (card.get("sources") or {}).items():
        if not isinstance(sources, dict):
            raise CardValidationError(f"sources.{country} must be a mapping")
        for source, weight in sources.items():
            if not isinstance(weight, (int, float)) or not 0 <= weight <= 1:
                raise CardValidationError(f"source weight {country}.{source} must be from 0 to 1")
    return card


def load_card(path: Path, defaults: dict[str, int]) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        card = yaml.safe_load(handle) or {}
    return validate_card(card, defaults)


def find_card(name: str, cards_dir: Path, defaults: dict[str, int]) -> tuple[dict[str, Any], Path] | None:
    candidates = [cards_dir / f"{name}.yaml"] + sorted(cards_dir.glob(f"{name}_*.yaml"))
    for path in candidates:
        if path.exists() and path.parent.name != "drafts":
            return load_card(path, defaults), path
    return None


def save_ephemeral_card(card: dict[str, Any], drafts_dir: Path, defaults: dict[str, int]) -> Path:
    draft = deepcopy(card)
    draft["reviewed"] = False
    draft["auto_written"] = True
    draft.pop("sources", None)
    validate_card(draft, defaults)
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{draft['category']}.yaml"
    path.write_text(yaml.safe_dump(draft, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def promote_card(name: str, cards_dir: Path, drafts_dir: Path, defaults: dict[str, int]) -> Path:
    source = drafts_dir / f"{name}.yaml"
    if not source.exists():
        raise FileNotFoundError(f"draft card not found: {name}")
    with source.open(encoding="utf-8") as handle:
        card = yaml.safe_load(handle) or {}
    card["reviewed"] = True
    card.pop("auto_written", None)
    validate_card(card, defaults)
    country = str(card.get("country", "generic")).lower()
    target = cards_dir / f"{name}_{country}.yaml"
    if target.exists():
        raise FileExistsError(f"reviewed card already exists: {target.name}")
    target.write_text(yaml.safe_dump(card, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target
