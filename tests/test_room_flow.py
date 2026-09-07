from copy import deepcopy
from datetime import datetime, timezone

from osusume.adapters import RecordedAdapters
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from tests.helpers import operational_details, operational_place
from tests.test_hotels import booking_row, hotel_request


NOW = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
ATTRIBUTE = {
    "claim_id": "suite_hot_tub",
    "claim_type": "layout",
    "text": "A private hot tub is in the suite",
    "synonyms": ["jacuzzi", "whirlpool"],
}


class Model:
    def __init__(self) -> None:
        self.calls = []

    def run(self, slot: str, payload: dict) -> dict:
        self.calls.append((slot, deepcopy(payload)))
        return {"judgments": []}


class Places:
    def __init__(self) -> None:
        self.photo_calls = []

    def photo(self, name: str) -> dict:
        self.photo_calls.append(name)
        return {"url": f"https://photos.example/{name}"}


def indexed_candidate(passages: list[dict], *, photos: list[dict] | None = None):
    parsed = StructuredRequest.from_dict(hotel_request([ATTRIBUTE]))
    candidate = Candidate.from_place(operational_place("p1", "Hotel One", "hotel"))
    candidate.raw["booking"] = booking_row()
    candidate.raw["_room_indexed"] = True
    candidate.raw["_room_attributes"] = [ATTRIBUTE]
    details = operational_details()["en"]
    details["photos"] = photos or []
    candidate.details = details
    config = load_config()
    model = Model()
    places = Places()
    funnel = Funnel(config, RecordedAdapters(places, None, model), now=NOW)
    candidate.ledger = funnel._build_ledger(
        candidate,
        parsed,
        {"sweep_source": "booking"},
        {"_room_indexed": True, "room_passages": passages},
        details,
        None,
        None,
    )
    return funnel, candidate, model, places


def test_cheap_room_proof_skips_judge_and_photos_but_computes_status_and_price() -> None:
    passage = {
        "room_name": "Penthouse",
        "text": "Penthouse: 90 m² terrace with jacuzzi",
        "page_url": "https://hotel.example/rooms",
        "source_kind": "official",
        "identity_label": "exact-venue",
    }
    funnel, candidate, model, places = indexed_candidate([passage], photos=[{"name": "one"}])

    funnel.stage5_judge([candidate], {})

    statuses = {claim.claim_id: claim.status.value for claim in candidate.ledger.claims}
    assert statuses["suite_hot_tub"] == "supported"
    assert statuses["operational_status"] == "supported"
    assert statuses["price"] == "supported"
    evidence = next(row for row in candidate.ledger.evidence if row.claim_id == "suite_hot_tub")
    assert evidence.quote == passage["text"]
    assert evidence.metadata["identity_label"] == "exact-venue"
    assert model.calls == []
    assert places.photo_calls == []


def test_ambiguous_passages_are_batched_into_one_judge_then_photos_run_if_unknown() -> None:
    passages = [
        {
            "room_name": "Wellness Suite",
            "text": "Wellness Suite: spa with jacuzzi (shared)",
            "page_url": "https://hotel.example/rooms",
            "source_kind": "official",
            "identity_label": "exact-venue",
        },
        {
            "room_name": "Terrace Suite",
            "text": "Terrace Suite: shared whirlpool",
            "page_url": "https://hotel.example/rooms",
            "source_kind": "official",
            "identity_label": "exact-venue",
        },
    ]
    funnel, candidate, model, places = indexed_candidate(passages, photos=[{"name": "one"}])

    funnel.stage5_judge([candidate], {})

    judge_calls = [payload for slot, payload in model.calls if slot == "judge"]
    assert len(judge_calls) == 1
    assert [row["text"] for row in judge_calls[0]["ledger"]["evidence"]] == [
        passage["text"] for passage in passages
    ]
    assert places.photo_calls == ["one"]
    assert [slot for slot, _ in model.calls] == ["judge", "photo_triage", "photo_read"]


def test_no_mention_without_photos_stays_unknown_and_never_calls_judge() -> None:
    passage = {
        "room_name": "Penthouse",
        "text": "Penthouse: a private 90 m² terrace",
        "page_url": "https://hotel.example/rooms",
        "source_kind": "official",
        "identity_label": "exact-venue",
    }
    funnel, candidate, model, places = indexed_candidate([passage])

    funnel.stage5_judge([candidate], {})

    claim = next(row for row in candidate.ledger.claims if row.claim_id == "suite_hot_tub")
    assert claim.status.value == "unknown"
    assert [slot for slot, _ in model.calls if slot == "judge"] == []
    assert places.photo_calls == []


def test_delayed_observed_photo_keeps_claim_binding_date_and_polarity() -> None:
    passage = {
        "room_name": "Penthouse",
        "text": "Penthouse: a private 90 m² terrace",
        "page_url": "https://hotel.example/rooms",
        "source_kind": "official",
        "identity_label": "exact-venue",
    }
    photos = [
        {"name": "wrong", "claim_id": "another_claim"},
        {
            "name": "observed",
            "claim_id": "suite_hot_tub",
            "observed_text": "The Penthouse photo shows a private jacuzzi.",
            "evidence_id": "observed_jacuzzi",
            "evidence_date": "2026-09-06T10:00:00+00:00",
            "polarity": "supports",
        },
    ]
    funnel, candidate, _, places = indexed_candidate([passage], photos=photos)

    class ObservedModel(Model):
        def run(self, slot: str, payload: dict) -> dict:
            self.calls.append((slot, deepcopy(payload)))
            if slot == "photo_read":
                row = payload["photos"][0]
                return {
                    "judgments": [{
                        "claim_id": row["claim_id"],
                        "evidence_id": row["evidence_id"],
                        "quote": row["quote"],
                        "entails": True,
                        "contradicts": False,
                    }]
                }
            return {"judgments": []}

    model = ObservedModel()
    funnel.adapters.model = model
    funnel.stage5_judge([candidate], {})

    assert places.photo_calls == ["observed"]
    assert [slot for slot, _ in model.calls] == ["photo_triage", "photo_read"]
    photo = next(row for row in candidate.ledger.evidence if row.evidence_id == "observed_jacuzzi")
    assert photo.claim_id == "suite_hot_tub"
    assert photo.text == "The Penthouse photo shows a private jacuzzi."
    assert photo.evidence_date == "2026-09-06T10:00:00+00:00"
    assert photo.polarity == "supports"
    claim = next(row for row in candidate.ledger.claims if row.claim_id == "suite_hot_tub")
    assert claim.status.value == "supported"
