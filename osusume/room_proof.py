from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable


TUB_TERMS = (
    "hot tub",
    "jacuzzi",
    "whirlpool",
    "spa bath",
    "bañera de hidromasaje",
    "banyera d'hidromassatge",
    "hidromasaje",
)
EXCEPTIONS = (
    "except",
    "not",
    "no",
    "without",
    "does not",
    "doesn't",
    "sin",
    "no incluye",
    "sans",
    "senza",
    "shared",
    "compartido",
)
GENERIC_ROOM_NAMES = {
    "accommodation",
    "accommodations",
    "habitacion",
    "habitaciones",
    "habitacio",
    "habitacions",
    "room",
    "rooms",
    "suite",
    "suites",
    "spa",
    "amenities",
    "facilities",
    "wellness",
}


def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value).casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "".join(c for c in folded if not unicodedata.combining(c))).split())


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {_normalized(phrase)} " in f" {_normalized(text)} "


def attribute_terms(attribute: Any) -> tuple[str, ...]:
    text = str(attribute.get("text") or "") if isinstance(attribute, dict) else str(getattr(attribute, "text", ""))
    synonyms = attribute.get("synonyms", []) if isinstance(attribute, dict) else getattr(attribute, "synonyms", [])
    rows = [str(item).strip() for item in synonyms if str(item).strip()] if isinstance(synonyms, (list, tuple)) else []
    if any(_contains(text, term) for term in TUB_TERMS) or any(
        _contains(synonym, term) or _contains(term, synonym) for synonym in rows for term in TUB_TERMS
    ):
        rows.extend(TUB_TERMS)
    if text.strip():
        rows.append(text.strip())
    return tuple(dict.fromkeys(_normalized(row) for row in rows if _normalized(row)))


def _named_room(value: str) -> bool:
    normalized = _normalized(value)
    return bool(normalized) and normalized not in GENERIC_ROOM_NAMES


@dataclass(frozen=True)
class RoomProofResult:
    status: str
    passage: dict[str, Any] | None = None
    candidate_passages: tuple[dict[str, Any], ...] = ()
    judgment: Any = None

    @property
    def proved(self) -> bool:
        return self.status == "proved"


def evaluate_room_proof(attribute: Any, passages: Iterable[dict[str, Any]]) -> RoomProofResult:
    terms = attribute_terms(attribute)
    if not terms:
        return RoomProofResult("unknown")
    tub_attribute = any(term == _normalized(alias) for term in terms for alias in TUB_TERMS)
    candidates: list[dict[str, Any]] = []
    for raw in passages:
        passage = dict(raw)
        text = str(passage.get("text") or "")
        if not any(_contains(text, term) for term in terms):
            continue
        candidates.append(passage)
        context = f"{passage.get('room_name') or ''} {text}"
        blocked = any(_contains(context, term) for term in EXCEPTIONS)
        if tub_attribute and _contains(context, "spa"):
            blocked = True
        if _named_room(str(passage.get("room_name") or "")) and not blocked:
            return RoomProofResult("proved", passage, tuple(candidates))
    return RoomProofResult("judge" if candidates else "unknown", candidate_passages=tuple(candidates))


def prove_room_attribute(
    attribute: Any,
    passages: Iterable[dict[str, Any]],
    judge: Callable[[list[dict[str, Any]]], Any] | None = None,
) -> RoomProofResult:
    result = evaluate_room_proof(attribute, passages)
    if result.status != "judge" or judge is None:
        return result
    judgment = judge(list(result.candidate_passages))
    return RoomProofResult("judged", candidate_passages=result.candidate_passages, judgment=judgment)


cheap_proof = evaluate_room_proof
