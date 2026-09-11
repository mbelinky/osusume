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
BARE_TUB_TERMS = {"banera", "banyera", "bathtub", "bath tub", "bath", "tub", "bano", "bany"}
ROOM_CATEGORY_TERMS = (
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
SHARED_CONTEXT_TERMS = (
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
PRIVATE_CONTEXT_TERMS = (
    "terraza privada",
    "private terrace",
    "in-room",
    "en la habitación",
    "en la suite",
)
SHARED_CONTEXT_NOTE = "shared-context words present"
# A sentence that opens with one of these continues the sentence before it
# ("Todas las habitaciones son amplias. Con bañera de hidromasaje ..."), so
# the room word of the previous sentence still ties the attribute.
CONTINUATION_STARTS = (
    "con",
    "with",
    "avec",
    "mit",
    "amb",
    "including",
    "incluye",
    "incluyen",
    "featuring",
    "equipped",
    "equipada",
    "equipadas",
    "equipado",
    "equipados",
    "dotada",
    "dotadas",
)
OPENING_HOURS = re.compile(r"\d{1,2}:\d{2}\s*h?\s*(?:a|to|-)\s*\d{1,2}:\d{2}", re.IGNORECASE)
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
    if text.strip():
        rows.append(text.strip())
    tub_attribute = any(_contains(row, term) for row in rows for term in TUB_TERMS)
    if tub_attribute:
        rows = [row for row in rows if _normalized(row) not in BARE_TUB_TERMS]
        rows.extend(TUB_TERMS)
    return tuple(dict.fromkeys(_normalized(row) for row in rows if _normalized(row)))


def _has_room_category(value: str) -> bool:
    return any(_contains(value, term) for term in ROOM_CATEGORY_TERMS)


def _continues_previous(sentence: str) -> bool:
    words = _normalized(sentence).split()
    return bool(words) and words[0] in CONTINUATION_STARTS


def _sentence_has_room_tie(text: str, terms: tuple[str, ...]) -> bool:
    sentences = [part for part in re.split(r"[.!?;\n]+", text) if part.strip()]
    for index, sentence in enumerate(sentences):
        if not any(_contains(sentence, term) for term in terms):
            continue
        if _has_room_category(sentence):
            return True
        if index and _continues_previous(sentence) and _has_room_category(sentences[index - 1]):
            return True
    return False


def _has_shared_context(value: str) -> bool:
    return any(_contains(value, term) for term in SHARED_CONTEXT_TERMS) or bool(OPENING_HOURS.search(value))


def _sentence_has_private_attribute_context(text: str, terms: tuple[str, ...]) -> bool:
    return any(
        any(_contains(sentence, private_term) for private_term in PRIVATE_CONTEXT_TERMS)
        and any(_contains(sentence, attribute_term) for attribute_term in terms)
        for sentence in re.split(r"[.!?;\n]+", text)
    )


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
        shared_context = _has_shared_context(f"{room_name} {text}") and not _sentence_has_private_attribute_context(
            text, terms
        )
        if shared_context:
            passage["room_proof_note"] = SHARED_CONTEXT_NOTE
        candidates.append(passage)
        context = f"{room_name} {text}"
        blocked = any(_contains(context, term) for term in EXCEPTIONS)
        room_tied = _has_room_category(room_name) or _sentence_has_room_tie(text, terms)
        if room_tied and not blocked and not shared_context:
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
