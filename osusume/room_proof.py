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
    "jetted tub",
    "bañera de hidromasaje",
    "banyera d'hidromassatge",
    "hidromasaje",
)
# A plain bathtub never satisfies a hot-tub attribute, whatever the request lists.
WEAK_TUB_TERMS = (
    "bañera",
    "banyera",
    "bathtub",
    "bath tub",
    "bath",
    "tub",
    "baño",
    "bany",
)
ROOM_WORDS = (
    "room",
    "rooms",
    "suite",
    "suites",
    "junior",
    "penthouse",
    "apartment",
    "apartments",
    "studio",
    "habitación",
    "habitaciones",
    "habitació",
    "habitacions",
    "chambre",
    "chambres",
    "zimmer",
    "camera",
    "camere",
)
# Words that mark a property-level or shared facility rather than a room.
SHARED_CONTEXT = (
    "rooftop",
    "azotea",
    "solarium",
    "piscina",
    "pool",
    "spa",
    "gimnasio",
    "gym",
    "wellness",
    "en nuestra terraza",
    "on our terrace",
    "de la última planta",
    "top floor",
    "exterior (de",
)
OPENING_HOURS = re.compile(r"\d{1,2}:\d{2}\s*h?\s*(?:a|to|-)\s*\d{1,2}:\d{2}", re.IGNORECASE)
# Phrases that make a tub private to the room even next to shared-context words.
PRIVATE_CONTEXT = (
    "terraza privada",
    "private terrace",
    "in-room",
    "en la habitación",
    "en la suite",
)
SHARED_CONTEXT_NOTE = "shared-context words present"
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

def _normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value).casefold())
    return " ".join(re.sub(r"[^a-z0-9]+", " ", "".join(c for c in folded if not unicodedata.combining(c))).split())


def _contains(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {_normalized(phrase)} " in f" {_normalized(text)} "


def attribute_terms(attribute: Any) -> tuple[str, ...]:
    text = str(attribute.get("text") or "") if isinstance(attribute, dict) else str(getattr(attribute, "text", ""))
    synonyms = attribute.get("synonyms", []) if isinstance(attribute, dict) else getattr(attribute, "synonyms", [])
    rows = [str(item).strip() for item in synonyms if str(item).strip()] if isinstance(synonyms, (list, tuple)) else []
    tub_attribute = any(_contains(text, term) for term in TUB_TERMS) or any(
        _contains(synonym, term) or _contains(term, synonym) for synonym in rows for term in TUB_TERMS
    )
    if tub_attribute:
        weak = {_normalized(term) for term in WEAK_TUB_TERMS}
        rows = [row for row in rows if _normalized(row) not in weak]
        rows.extend(TUB_TERMS)
    if text.strip():
        rows.append(text.strip())
    return tuple(dict.fromkeys(_normalized(row) for row in rows if _normalized(row)))


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?;])\s+|\n+", text) if part.strip()]


def _room_tied(room_name: str, text: str, terms: tuple[str, ...]) -> bool:
    if any(_contains(room_name, word) for word in ROOM_WORDS):
        return True
    return any(_contains(text, word) for word in ROOM_WORDS) and any(_contains(text, term) for term in terms)


def _shared_context(value: str) -> bool:
    return any(_contains(value, word) for word in SHARED_CONTEXT) or bool(OPENING_HOURS.search(value))


def _privately_tied(text: str, terms: tuple[str, ...]) -> bool:
    for sentence in _sentences(text):
        if any(_contains(sentence, term) for term in terms) and any(
            _contains(sentence, phrase) for phrase in PRIVATE_CONTEXT
        ):
            return True
    return False


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
    candidates: list[dict[str, Any]] = []
    for raw in passages:
        passage = dict(raw)
        text = str(passage.get("text") or "")
        if not any(_contains(text, term) for term in terms):
            continue
        room_name = str(passage.get("room_name") or "")
        context = f"{room_name} {text}"
        shared = _shared_context(context) and not _privately_tied(text, terms)
        if shared:
            passage["note"] = SHARED_CONTEXT_NOTE
        candidates.append(passage)
        blocked = shared or any(_contains(context, term) for term in EXCEPTIONS)
        if _room_tied(room_name, text, terms) and not blocked:
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
