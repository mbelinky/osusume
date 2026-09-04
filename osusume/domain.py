from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Candidate:
    place_id: str
    name: str
    business_status: str = "BUSINESS_STATUS_UNSPECIFIED"
    location: dict[str, float] = field(default_factory=dict)
    rating: float | None = None
    review_count: int | None = None
    primary_type: str | None = None
    source_weight: float = 0.0
    registry: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    rejection_reason: str | None = None
    minutes: float | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[Any] = field(default_factory=list)
    ledger: Any = None
    verdict: str | None = None
    proposed_contact: dict[str, Any] | None = None

    @classmethod
    def from_place(cls, data: dict[str, Any]) -> "Candidate":
        place_id = data.get("place_id") or data.get("id") or data.get("name", "").removeprefix("places/")
        display = data.get("displayName") or data.get("display_name") or data.get("name") or place_id
        if isinstance(display, dict):
            display = display.get("text", place_id)
        return cls(
            place_id=str(place_id),
            name=str(display),
            business_status=data.get("business_status") or data.get("businessStatus") or "BUSINESS_STATUS_UNSPECIFIED",
            location=data.get("location") or {},
            rating=data.get("rating"),
            review_count=data.get("review_count") or data.get("userRatingCount"),
            primary_type=data.get("primary_type") or data.get("primaryType"),
            raw=data,
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("ledger", None)
        result["evidence"] = [item.to_dict() if hasattr(item, "to_dict") else item for item in self.evidence]
        return result


@dataclass(frozen=True)
class StructuredRequest:
    ask: str
    category: str
    country: str = "IT"
    local_language: str = "it"
    required_attributes: tuple[dict[str, str], ...] = ()
    scope: dict[str, Any] = field(default_factory=dict)
    arrival_start: str | None = None
    arrival_end: str | None = None
    max_detour_min: float | None = None
    exclusions: tuple[str, ...] = ()
    preferences: tuple[dict[str, str], ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StructuredRequest":
        return cls(
            ask=data.get("ask", ""),
            category=data.get("category", "generic"),
            country=data.get("country", "IT"),
            local_language=data.get("local_language", "it"),
            required_attributes=tuple(data.get("required_attributes", ())),
            scope=data.get("scope", {}),
            arrival_start=data.get("arrival_start"),
            arrival_end=data.get("arrival_end"),
            max_detour_min=data.get("max_detour_min"),
            exclusions=tuple(data.get("exclusions", ())),
            preferences=tuple(data.get("preferences", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
