from __future__ import annotations

import re
import unicodedata
from datetime import date
from math import asin, cos, isfinite, radians, sin, sqrt
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_DIR = ROOT / "registry"
GENERIC_NAME_WORDS = {
    "cocina",
    "restaurant",
    "restaurante",
    "ristorante",
}
# Words a guide or Places may add to the same restaurant name on either side.
GENERIC_NAME_TOKENS = GENERIC_NAME_WORDS | {
    "at",
    "by",
    "cuina",
    "cuisine",
    "de",
    "del",
    "el",
    "els",
    "gastronomic",
    "gastronomico",
    "la",
    "the",
}
# NFKD leaves these letters out of ASCII entirely, so fold them first.
_FOLDED_LETTERS = str.maketrans({
    "ø": "o", "Ø": "O",
    "ł": "l", "Ł": "L",
    "æ": "ae", "Æ": "AE",
    "œ": "oe", "Œ": "OE",
    "ß": "ss",
    "đ": "d", "Đ": "D",
})


class GuideRegistryError(ValueError):
    pass


# Every guide the registry accepts, with the official domain its URLs must use.
GUIDES = {
    "michelin": "guide.michelin.com",
    "repsol": "guiarepsol.com",
    "fifty_best": "theworlds50best.com",
    "macarfi": "macarfi.com",
    "hardens": "hardens.com",
    "le_fooding": "lefooding.com",
    "michelin_bib": "guide.michelin.com",
}


def normalized_text(value: Any) -> str:
    plain = unicodedata.normalize("NFKD", str(value or "").translate(_FOLDED_LETTERS))
    return " ".join(re.findall(r"[a-z0-9]+", plain.encode("ascii", "ignore").decode().casefold()))


def _canonical_name(value: Any) -> str:
    parts = normalized_text(value).split()
    while parts and parts[0] in GENERIC_NAME_WORDS:
        parts.pop(0)
    return " ".join(parts)


def names_match(left: Any, right: Any) -> bool:
    a = _canonical_name(left)
    b = _canonical_name(right)
    return bool(a and b and a == b)


def _significant_tokens(value: Any, place_tokens: frozenset[str] = frozenset()) -> list[str]:
    """Name tokens that identify a restaurant: no generic words, no place names."""
    head = re.split(r"\s+at\s+", str(value or ""), maxsplit=1, flags=re.IGNORECASE)[0]
    return [
        token
        for token in normalized_text(head).split()
        if token not in GENERIC_NAME_TOKENS and token not in place_tokens
    ]


def _tokens_identify_same_name(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    if all(token in longer for token in shorter):
        return True
    return left[0] == right[0] and len(left[0]) >= 4 and min(len(left), len(right)) <= 2


def _coordinates(row: dict[str, Any]) -> tuple[float, float] | None:
    location = row.get("location") or {}
    latitude = row.get("latitude", location.get("latitude", location.get("lat")))
    longitude = row.get("longitude", location.get("longitude", location.get("lng")))
    if latitude is None or longitude is None:
        return None
    return float(latitude), float(longitude)


def distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    left_lat, left_lng = map(radians, left)
    right_lat, right_lng = map(radians, right)
    delta_lat = right_lat - left_lat
    delta_lng = right_lng - left_lng
    value = sin(delta_lat / 2) ** 2 + cos(left_lat) * cos(right_lat) * sin(delta_lng / 2) ** 2
    return 2 * 6371.0088 * asin(min(1.0, sqrt(value)))


def _validate_entry(entry: Any, path: Path, index: int) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise GuideRegistryError(f"{path}: entries[{index}] must be a mapping")
    required = ("name", "locality", "province", "guide", "level", "url", "verified_at")
    missing = [field for field in required if entry.get(field) in (None, "")]
    if missing:
        raise GuideRegistryError(f"{path}: entries[{index}] missing {', '.join(missing)}")
    guide = str(entry["guide"]).casefold()
    if guide not in GUIDES:
        raise GuideRegistryError(f"{path}: entries[{index}].guide must be one of {', '.join(GUIDES)}")
    level = entry["level"]
    if type(level) is not int:
        raise GuideRegistryError(f"{path}: entries[{index}].level must be an integer 1, 2, or 3")
    if level not in {1, 2, 3}:
        raise GuideRegistryError(f"{path}: entries[{index}].level must be 1, 2, or 3")
    url = str(entry["url"])
    parsed_url = urlparse(url)
    hostname = (parsed_url.hostname or "").casefold()
    official_domain = GUIDES[guide]
    if parsed_url.scheme != "https" or (hostname != official_domain and not hostname.endswith(f".{official_domain}")):
        raise GuideRegistryError(f"{path}: entries[{index}].url must use {official_domain}")
    raw_verified_at = str(entry["verified_at"])
    try:
        parsed_verified_at = date.fromisoformat(raw_verified_at)
    except ValueError as exc:
        raise GuideRegistryError(f"{path}: entries[{index}].verified_at must be a canonical ISO date") from exc
    if parsed_verified_at.isoformat() != raw_verified_at:
        raise GuideRegistryError(f"{path}: entries[{index}].verified_at must be a canonical ISO date")
    aliases = entry.get("aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
        raise GuideRegistryError(f"{path}: entries[{index}].aliases must be a list of non-empty strings")
    if "city" in entry and (not isinstance(entry["city"], str) or not entry["city"].strip()):
        raise GuideRegistryError(f"{path}: entries[{index}].city must be a non-empty string")
    normalized = dict(entry)
    normalized.update({"guide": guide, "level": level, "aliases": aliases, "verified_at": raw_verified_at})
    for coordinate in ("latitude", "longitude"):
        if coordinate in normalized:
            value = normalized[coordinate]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise GuideRegistryError(f"{path}: entries[{index}].{coordinate} must be numeric")
            normalized[coordinate] = float(value)
    if ("latitude" in normalized) != ("longitude" in normalized):
        raise GuideRegistryError(f"{path}: entries[{index}] must declare both latitude and longitude")
    if "latitude" in normalized and (
        not isfinite(normalized["latitude"])
        or not isfinite(normalized["longitude"])
        or not -90 <= normalized["latitude"] <= 90
        or not -180 <= normalized["longitude"] <= 180
    ):
        raise GuideRegistryError(f"{path}: entries[{index}] coordinates are out of range")
    return normalized


def load_guide_registry(country: str, registry_dir: Path | None = None) -> list[dict[str, Any]] | None:
    if not isinstance(country, str) or not re.fullmatch(r"[A-Za-z]{2}", country):
        raise GuideRegistryError("country must be a two-letter ISO code")
    path = (registry_dir or DEFAULT_REGISTRY_DIR) / f"{country.casefold()}_restaurants.yaml"
    if not path.exists():
        return None
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if payload.get("format_version") != 1:
        raise GuideRegistryError(f"{path}: format_version must be 1")
    if str(payload.get("country", "")).upper() != country.upper():
        raise GuideRegistryError(f"{path}: country must be {country.upper()}")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise GuideRegistryError(f"{path}: entries must be a list")
    return [_validate_entry(entry, path, index) for index, entry in enumerate(entries)]


def _entry_place_tokens(entry: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        normalized_text(entry.get("locality")).split() + normalized_text(entry.get("province")).split()
    )


def registry_entry_matches_candidate(entry: dict[str, Any], candidate: dict[str, Any]) -> bool:
    candidate_names = [candidate.get("name", "")]
    display = candidate.get("displayName") or candidate.get("display_name")
    if isinstance(display, dict):
        candidate_names.append(display.get("text", ""))
    elif display:
        candidate_names.append(display)
    place_tokens = _entry_place_tokens(entry)
    registry_tokens = [_significant_tokens(name, place_tokens) for name in (entry["name"], *entry.get("aliases", []))]
    candidate_tokens = [_significant_tokens(name, place_tokens) for name in candidate_names]
    pairs = [(left, right) for left in registry_tokens for right in candidate_tokens]

    entry_coordinates = _coordinates(entry)
    candidate_coordinates = _coordinates(candidate)
    if entry_coordinates and candidate_coordinates:
        if distance_km(entry_coordinates, candidate_coordinates) > 0.3:
            return False
        # Proximity is not identity. Three hundred metres of a dense city holds
        # dozens of unrelated restaurants, and accepting one shared word once
        # handed a fried-chicken chain 299 m away another venue's two Michelin
        # stars. Coordinates only waive the locality check below; the names must
        # still identify a single venue.
        return any(_tokens_identify_same_name(left, right) for left, right in pairs)

    if not any(_tokens_identify_same_name(left, right) for left, right in pairs):
        return False

    address = normalized_text(
        candidate.get("formattedAddress")
        or candidate.get("formatted_address")
        or candidate.get("address")
        or ""
    )
    if not address:
        return False
    locality = normalized_text(entry.get("locality"))
    return bool(locality and locality in address)


def entry_query(entry: dict[str, Any]) -> str:
    parts = [str(entry["name"]), str(entry.get("locality") or ""), str(entry.get("province") or "")]
    unique: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tokens = set(normalized_text(part).split())
        if not tokens or tokens <= seen:
            continue
        unique.append(part)
        seen |= tokens
    return " ".join(unique)


def entry_in_text_scope(entry: dict[str, Any], request: dict[str, Any]) -> bool:
    scope = request.get("scope") or {}
    if scope.get("kind") == "route":
        scope_text = " ".join(str(scope.get(field) or "") for field in ("from", "to", "city", "locality"))
    else:
        scope_text = " ".join(str(scope.get(field) or "") for field in ("place", "city", "locality"))
    scope_text = normalized_text(f"{scope_text} {request.get('ask', '')}")
    if not scope_text:
        return False
    locality = normalized_text(entry.get("locality"))
    region = normalized_text(entry.get("region"))
    province = normalized_text(entry.get("province"))
    explicit_province = normalized_text(scope.get("province"))
    return bool(
        (locality and locality in scope_text)
        or (region and region in scope_text)
        or (province and explicit_province and province == explicit_province)
    )
