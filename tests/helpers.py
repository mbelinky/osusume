from __future__ import annotations

from copy import deepcopy


class FakePlaces:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture

    def sweep(self, request: dict, card: dict) -> dict:
        return {"candidates": deepcopy(self.fixture.get("sweep", []))}

    def resolve(self, name: str, request: dict, place_type: str | None = None) -> dict | None:
        return deepcopy(self.fixture.get("resolved", {}).get(name))

    def details(self, place_id: str, local_language: str) -> dict:
        return deepcopy(self.fixture.get("details", {})[place_id])

    def directions(
        self,
        start: str,
        end: str,
        departure_time: str | None = None,
        *,
        start_is_place_id: bool = False,
        end_is_place_id: bool = False,
        mode: str = "drive",
    ) -> dict:
        return deepcopy(self.fixture.get("directions", {})[f"{start}|{end}"])

    def photo(self, photo_name: str) -> dict:
        return deepcopy(self.fixture.get("photos", {}).get(photo_name, {}))


class FakeWeb:
    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture

    def registry(self, request: dict, card: dict, candidates: list[dict] | None = None) -> dict:
        return deepcopy(self.fixture.get("registry", {"qualifications": [], "injected": []}))

    def mine(self, candidate: dict, request: dict, card: dict) -> dict:
        return deepcopy(self.fixture.get("mined", {}).get(candidate["place_id"], {"pages": [], "evidence": []}))

    def official_pages(self, candidate: dict, details: dict) -> dict:
        return deepcopy(self.fixture.get("official_pages", {}).get(candidate["place_id"], {"pages": [], "evidence": []}))


class FakeModel:
    def __init__(self, request: dict) -> None:
        self.request = request

    def run(self, slot: str, payload: dict) -> dict:
        if slot == "parse":
            return {"request": deepcopy(self.request)}
        if slot == "judge":
            judgments = []
            for evidence in payload["ledger"]["evidence"]:
                judgments.append(
                    {
                        "claim_id": evidence["claim_id"],
                        "evidence_id": evidence["evidence_id"],
                        "quote": evidence["quote"],
                        "entails": evidence.get("polarity") != "contradicts",
                        "contradicts": evidence.get("polarity") == "contradicts",
                        "status": "supported",
                    }
                )
            return {"judgments": judgments}
        if slot == "assemble":
            return {"ignored_extra_claim": "This must never render"}
        if slot in {"photo_triage", "photo_read"}:
            return {"judgments": []}
        raise AssertionError(slot)


def operational_place(place_id: str = "p1", name: str = "Test Place", primary_type: str = "food_store") -> dict:
    return {
        "id": place_id,
        "displayName": {"text": name},
        "businessStatus": "OPERATIONAL",
        "location": {"latitude": 42.0, "longitude": 12.0},
        "rating": 4.6,
        "userRatingCount": 171,
        "primaryType": primary_type,
    }


def operational_details(place_id: str = "p1", status: str = "OPERATIONAL") -> dict:
    body = {
        "id": place_id,
        "displayName": {"text": "Test Place"},
        "businessStatus": status,
        "location": {"latitude": 42.0, "longitude": 12.0},
        "regularOpeningHours": {
            "periods": [
                {
                    "open": {"day": 3, "hour": 9, "minute": 0},
                    "close": {"day": 3, "hour": 20, "minute": 0},
                }
            ]
        },
    }
    return {"en": deepcopy(body), "local": deepcopy(body)}


def request(required: list[dict] | None = None, *, route: bool = False) -> dict:
    scope = {"kind": "route", "from": "A", "to": "B", "radius_km": 1.0} if route else {"kind": "near", "lat": 42.0, "lng": 12.0, "radius_km": 5.0}
    return {
        "ask": "test recommendation",
        "category": "salumeria",
        "country": "IT",
        "local_language": "it",
        "required_attributes": required or [],
        "scope": scope,
        "arrival_start": "2026-08-26T12:00:00+02:00",
        "arrival_end": "2026-08-26T13:00:00+02:00",
        "max_detour_min": 15.0 if route else None,
        "exclusions": [],
        "preferences": [],
    }
