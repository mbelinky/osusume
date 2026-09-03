from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from urllib.parse import urlparse


class ClaimStatus(str, Enum):
    SUPPORTED = "supported"
    STALE = "stale"
    WRONG_SOURCE = "wrong_source"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"
    REFUTED = "refuted"


class SourceClass(str, Enum):
    OFFICIAL = "official"
    PLACES = "places"
    PHOTO = "photo"
    GUIDE_ENTRY = "guide_entry"
    LOCAL_PRESS = "local_press"
    REVIEW = "review"
    MENTION = "mention"
    NEVER = "never_load_bearing"


TIERS: dict[SourceClass, int | None] = {
    SourceClass.OFFICIAL: 0,
    SourceClass.PLACES: 1,
    SourceClass.PHOTO: 2,
    SourceClass.GUIDE_ENTRY: 3,
    SourceClass.LOCAL_PRESS: 3,
    SourceClass.REVIEW: 4,
    SourceClass.MENTION: None,
    SourceClass.NEVER: None,
}

NEVER_LOAD_BEARING_KINDS = {
    "travel_blog",
    "listicle",
    "delivery_app",
    "search_snippet",
    "aggregate_rating",
    "caller_note",
    "agent_note",
    "prior_plan",
}


def registrable_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in {"co.uk", "org.uk", "com.au", "com.br", "co.jp", "co.nz"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def classify_source(kind: str, *, roundup: bool = False) -> SourceClass:
    normalized = kind.lower()
    if normalized in NEVER_LOAD_BEARING_KINDS:
        return SourceClass.NEVER
    if roundup:
        return SourceClass.MENTION
    if normalized in {"official_site", "official_menu", "official_social", "venue_reply", "municipal_calendar", "consortium"}:
        return SourceClass.OFFICIAL
    if normalized in {"places", "places_field", "computed_route"}:
        return SourceClass.PLACES
    if normalized in {"photo", "menu_photo"}:
        return SourceClass.PHOTO
    if normalized in {"qualified_guide", "rated_guide_entry"}:
        return SourceClass.GUIDE_ENTRY
    if normalized == "local_press":
        return SourceClass.LOCAL_PRESS
    if normalized == "review":
        return SourceClass.REVIEW
    return SourceClass.NEVER


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    claim_id: str
    source_kind: str
    url: str
    fetched_at: str
    evidence_date: str
    text: str
    quote: str = ""
    polarity: str = "supports"
    roundup: bool = False
    source_class: SourceClass | None = None
    domain: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fetched_at or not self.evidence_date:
            raise ValueError("every evidence record needs fetched_at and evidence_date")
        if self.source_class is None:
            object.__setattr__(self, "source_class", classify_source(self.source_kind, roundup=self.roundup))
        if not self.domain:
            object.__setattr__(self, "domain", registrable_domain(self.url))

    @property
    def tier(self) -> int | None:
        return TIERS[self.source_class]

    def quote_is_literal(self, quote: str | None = None) -> bool:
        candidate = self.quote if quote is None else quote
        return bool(candidate) and candidate in self.text

    def age_days(self, now: datetime) -> int:
        raw = self.evidence_date or self.fetched_at
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, (now.date() - parsed.astimezone(timezone.utc).date()).days)

    def to_dict(self) -> dict:
        result = asdict(self)
        result["source_class"] = self.source_class.value
        result["tier"] = self.tier
        return result


@dataclass
class Claim:
    claim_id: str
    text: str
    claim_type: str
    required: bool = False
    status: ClaimStatus = ClaimStatus.UNKNOWN
    evidence_ids: list[str] = field(default_factory=list)
    qualified_evidence_ids: list[str] = field(default_factory=list)
    evidence_clause: str = "unverified"
    drop_count: int = 0

    def to_dict(self) -> dict:
        result = asdict(self)
        result["status"] = self.status.value
        return result


ALLOWED_SOURCE_CLASSES: dict[str, set[SourceClass]] = {
    "operational_status": {SourceClass.OFFICIAL, SourceClass.PLACES},
    "hours_at_arrival": {SourceClass.OFFICIAL, SourceClass.PLACES},
    "detour": {SourceClass.PLACES},
    "proximity": {SourceClass.PLACES},
    "counter_service": {SourceClass.OFFICIAL, SourceClass.PHOTO},
    "layout": {SourceClass.OFFICIAL, SourceClass.PHOTO},
    "product_inventory": {SourceClass.OFFICIAL, SourceClass.PHOTO, SourceClass.PLACES, SourceClass.REVIEW},
    "vegetarian_options": {SourceClass.OFFICIAL, SourceClass.PHOTO, SourceClass.PLACES, SourceClass.REVIEW},
    "quality": {SourceClass.GUIDE_ENTRY, SourceClass.LOCAL_PRESS},
    "importance": {SourceClass.OFFICIAL},
    "event_schedule": {SourceClass.OFFICIAL},
    "rating_signal": {SourceClass.PLACES},
    "venue_type": {SourceClass.OFFICIAL, SourceClass.PLACES},
    "generic": {SourceClass.OFFICIAL, SourceClass.PLACES, SourceClass.PHOTO, SourceClass.GUIDE_ENTRY, SourceClass.LOCAL_PRESS, SourceClass.REVIEW},
}


class ClaimLedger:
    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}
        self._evidence: dict[str, EvidenceRecord] = {}
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    @property
    def claims(self) -> tuple[Claim, ...]:
        return tuple(self._claims.values())

    @property
    def evidence(self) -> tuple[EvidenceRecord, ...]:
        return tuple(self._evidence.values())

    def add_claim(self, claim: Claim) -> None:
        if self._frozen:
            raise RuntimeError("claim ledger is frozen")
        existing = self._claims.get(claim.claim_id)
        if existing and (existing.text != claim.text or existing.claim_type != claim.claim_type):
            raise ValueError(f"claim id {claim.claim_id} has conflicting definitions")
        self._claims.setdefault(claim.claim_id, claim)

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        if self._frozen:
            raise RuntimeError("claim ledger is frozen")
        if evidence.claim_id not in self._claims:
            raise KeyError(f"unknown claim {evidence.claim_id}")
        self._evidence[evidence.evidence_id] = evidence
        self._claims[evidence.claim_id].evidence_ids.append(evidence.evidence_id)

    def freeze(self) -> None:
        self._frozen = True

    def compute(
        self,
        judgments: list[dict],
        freshness_days: dict[str, int],
        *,
        now: datetime,
    ) -> None:
        if not self._frozen:
            raise RuntimeError("freeze the claim ledger before computing status")
        accepted: dict[str, list[tuple[EvidenceRecord, str]]] = {key: [] for key in self._claims}
        for judgment in judgments:
            claim = self._claims.get(judgment.get("claim_id", ""))
            evidence = self._evidence.get(judgment.get("evidence_id", ""))
            if claim is None or evidence is None or evidence.claim_id != claim.claim_id:
                continue
            quote = judgment.get("quote", "")
            if not evidence.quote_is_literal(quote):
                claim.drop_count += 1
                continue
            relation = "contradicts" if judgment.get("contradicts") else ("supports" if judgment.get("entails") else "irrelevant")
            if relation != "irrelevant":
                accepted[claim.claim_id].append((evidence, relation))

        for claim in self._claims.values():
            rows = accepted[claim.claim_id]
            allowed = ALLOWED_SOURCE_CLASSES.get(claim.claim_type, ALLOWED_SOURCE_CLASSES["generic"])
            threshold = freshness_days.get(claim.claim_type, freshness_days["generic"])
            right_source = [
                (item, relation)
                for item, relation in rows
                if item.source_class in allowed and self._kind_allowed(claim.claim_type, item)
            ]
            fresh_by_age = [(item, relation) for item, relation in right_source if item.age_days(now) <= threshold]
            fresh = list(fresh_by_age)
            if claim.claim_type in {"product_inventory", "vegetarian_options"}:
                review_domains = {item.domain for item, relation in fresh if item.source_class == SourceClass.REVIEW and relation == "supports"}
                if len(review_domains) < 2:
                    fresh = [(item, relation) for item, relation in fresh if item.source_class != SourceClass.REVIEW]
            support = [item for item, relation in fresh if relation == "supports"]
            contrary = [item for item, relation in fresh if relation == "contradicts"]
            if support and contrary:
                claim.status = ClaimStatus.CONFLICT
                qualified = support + contrary
            elif contrary:
                claim.status = ClaimStatus.REFUTED
                qualified = contrary
            elif support:
                claim.status = ClaimStatus.SUPPORTED
                qualified = support
            elif right_source and not fresh_by_age:
                claim.status = ClaimStatus.STALE
                qualified = [item for item, _ in right_source]
            elif rows and not right_source:
                claim.status = ClaimStatus.WRONG_SOURCE
                qualified = []
            else:
                claim.status = ClaimStatus.UNKNOWN
                qualified = []
            claim.qualified_evidence_ids = [item.evidence_id for item in qualified]
            if qualified:
                best = min(qualified, key=lambda item: 99 if item.tier is None else item.tier)
                claim.evidence_clause = f"{best.source_class.value}, dated {best.evidence_date}"
            elif rows:
                claim.evidence_clause = "evidence did not meet the source or freshness rule"
            else:
                claim.evidence_clause = "unverified"

    @staticmethod
    def _kind_allowed(claim_type: str, evidence: EvidenceRecord) -> bool:
        if claim_type in {"importance", "event_schedule"}:
            return evidence.source_kind in {"municipal_calendar", "consortium"}
        if claim_type == "vegetarian_options" and evidence.source_class == SourceClass.PLACES:
            return evidence.metadata.get("field") == "servesVegetarianFood"
        if claim_type == "product_inventory" and evidence.source_class == SourceClass.PLACES:
            return evidence.metadata.get("field") in {"menuUri", "servesVegetarianFood"}
        return True

    def to_dict(self) -> dict:
        return {
            "frozen": self._frozen,
            "claims": [claim.to_dict() for claim in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
        }
