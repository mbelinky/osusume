from copy import deepcopy
from datetime import datetime, timezone

import pytest

from osusume.adapters import AdapterError, BookingAdapter, GoplacesAdapter, RecordedAdapters
from osusume.cards import CardValidationError, load_card, validate_card
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place


NOW = datetime(2026, 9, 4, 10, tzinfo=timezone.utc)


def booking_row(name: str = "Hotel Uno", slug: str = "hotel-uno") -> dict:
    return {
        "name": name,
        "slug": slug,
        "country": "es",
        "url": f"https://www.booking.com/hotel/es/{slug}.html",
        "price": 740,
        "currency": "EUR",
        "review_score": 9.1,
        "review_count": 412,
        "stars": 4,
        "distance_km": 0.7,
        "free_cancellation": True,
        "breakfast_included": False,
    }


def hotel_request(required: list[dict] | None = None, *, stay: bool = True) -> dict:
    result = {
        "ask": "hotel in Barcelona",
        "category": "hotel",
        "country": "ES",
        "local_language": "es",
        "required_attributes": required or [],
        "scope": {"kind": "near", "city": "Barcelona", "lat": 42.0, "lng": 12.0, "radius_km": 5},
        "arrival_start": None,
        "arrival_end": None,
        "max_detour_min": None,
        "exclusions": [],
        "preferences": [],
        "hotel_filters": {},
    }
    if stay:
        result["stay"] = {"check_in": "2026-10-01", "check_out": "2026-10-03", "adults": 2}
    return result


class FakeBooking:
    def __init__(self, rows: list[dict], details: dict | None = None) -> None:
        self.rows = rows
        self.detail_payload = details or {"facilities": []}
        self.sweep_calls = []
        self.detail_calls = []

    def sweep(self, request: dict, card: dict) -> dict:
        self.sweep_calls.append((deepcopy(request), deepcopy(card)))
        return {"candidates": [{"name": row["name"], "raw": {"booking": deepcopy(row)}} for row in self.rows]}

    def details(self, country: str, slug: str) -> dict:
        self.detail_calls.append((country, slug))
        return deepcopy(self.detail_payload)


def run_hotel(tmp_path, parsed: dict, booking: FakeBooking, places_fixture: dict, web_fixture: dict | None = None) -> dict:
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    return Funnel(
        config,
        RecordedAdapters(
            FakePlaces(places_fixture),
            FakeWeb(web_fixture or {}),
            FakeModel(parsed),
            booking=booking,
        ),
        now=NOW,
    ).run({"ask": parsed["ask"], "card": "hotel", "depth": "full"})


def test_booking_sweep_builds_command_and_maps_rows(monkeypatch) -> None:
    adapter = BookingAdapter("booking-test")
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"results": [booking_row()]})
    request = hotel_request()
    request["scope"] = {"kind": "anchor", "place": "Plaça de Catalunya", "lat": 41.39, "lng": 2.17}
    request["hotel_filters"] = {
        "min_stars": 4,
        "max_stars": 5,
        "min_score": 8,
        "pets": True,
        "breakfast": True,
        "free_cancellation": True,
    }

    result = adapter.sweep(request, {"sweep_source": "booking"})

    assert commands == [[
        "hotels", "list", "--query", "Plaça de Catalunya", "--checkin", "2026-10-01",
        "--checkout", "2026-10-03", "--adults", "2", "--currency", "EUR", "--nflt",
        "class=4;class=5;review_score=80;hotelfacility=4;mealplan=1;fc=2", "--order",
        "distance_from_search",
    ]]
    assert result["candidates"][0]["raw"]["booking"]["slug"] == "hotel-uno"


def test_booking_query_uses_near_city_or_route_destination() -> None:
    near = hotel_request()
    route = hotel_request()
    route["scope"] = {"kind": "route", "from": "Girona", "to": "Barcelona"}

    assert BookingAdapter._query(near) == "Barcelona"
    assert BookingAdapter._query(route) == "Barcelona"


def test_booking_adapter_rejects_missing_stay_dates() -> None:
    with pytest.raises(AdapterError, match="stay_dates_missing"):
        BookingAdapter("booking-test").sweep(hotel_request(stay=False), {})


def test_places_resolution_adds_optional_hotel_type(monkeypatch) -> None:
    adapter = GoplacesAdapter()
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"places": []})

    adapter.resolve("BLESS Barcelona", hotel_request(), "hotel")

    assert commands == [[
        "search", "BLESS Barcelona", "--limit", "1", "--type", "hotel",
        "--lat", "42.0", "--lng", "12.0", "--radius-m", "5000",
    ]]


def test_cli_stay_fields_override_parsed_values(tmp_path) -> None:
    parsed = hotel_request()
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    engine = Funnel(config, RecordedAdapters(None, None, FakeModel(parsed)), now=NOW)

    structured, _ = engine.stage0_parse({"ask": parsed["ask"], "card": "hotel", "stay": {"adults": 4}})

    assert structured.stay == {"check_in": "2026-10-01", "check_out": "2026-10-03", "adults": 4}


def test_booking_candidate_resolves_and_price_is_supported_while_unresolved_is_rejected(tmp_path) -> None:
    parsed = hotel_request()
    resolved = operational_place(name="Hotel Uno", primary_type="hotel")
    places = {
        "resolved": {"Hotel Uno": resolved},
        "details": {"p1": operational_details()},
    }
    output = run_hotel(tmp_path, parsed, FakeBooking([booking_row(), booking_row("Hotel Missing", "missing")]), places)

    cleared = next(row for row in output["candidates"] if row["name"] == "Hotel Uno")
    missing = next(row for row in output["candidates"] if row["name"] == "Hotel Missing")
    price = next(claim for claim in cleared["claims"] if claim["claim_id"] == "price")

    assert cleared["verdict"] == "cleared"
    assert cleared["booking_url"] == booking_row()["url"]
    assert price["status"] == "supported"
    assert "total EUR 740 for 2026-10-01 to 2026-10-03" in output["human"]
    assert missing["reason"] == "unresolved_listing"


def test_booking_resolution_uses_lodging_type_and_rejects_non_lodging(tmp_path) -> None:
    class CapturingPlaces(FakePlaces):
        def __init__(self, fixture: dict) -> None:
            super().__init__(fixture)
            self.resolve_types = []

        def resolve(self, name: str, request: dict, place_type: str | None = None) -> dict | None:
            self.resolve_types.append(place_type)
            return super().resolve(name, request, place_type)

    parsed = hotel_request()
    places = CapturingPlaces({
        "resolved": {
            "Hotel Bar": operational_place("bar", "Hotel Bar", "bar"),
            "Hotel Uno": operational_place(name="Hotel Uno", primary_type="hotel"),
        },
        "details": {"p1": operational_details()},
    })
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(
        config,
        RecordedAdapters(
            places,
            FakeWeb({}),
            FakeModel(parsed),
            booking=FakeBooking([booking_row("Hotel Bar", "hotel-bar"), booking_row()]),
        ),
        now=NOW,
    ).run({"ask": parsed["ask"], "card": "hotel", "depth": "full"})

    rejected = next(row for row in output["candidates"] if row["name"] == "Hotel Bar")
    accepted = next(row for row in output["candidates"] if row["name"] == "Hotel Uno")
    assert places.resolve_types == ["hotel", "hotel"]
    assert rejected["reason"] == "unresolved_listing"
    assert accepted["verdict"] == "cleared"


def test_booking_and_places_coordinates_over_300_metres_apart_are_unresolved(tmp_path) -> None:
    parsed = hotel_request()
    resolved = operational_place(name="Hotel Uno", primary_type="hotel")
    places = {"resolved": {"Hotel Uno": resolved}, "details": {"p1": operational_details()}}
    booking_details = {"latitude": 42.0045, "longitude": 12.0, "facilities": []}

    output = run_hotel(tmp_path, parsed, FakeBooking([booking_row()], booking_details), places)

    assert output["candidates"][0]["reason"] == "unresolved_listing"


def test_booking_ledger_omits_hours_while_places_ledger_keeps_it() -> None:
    parsed = hotel_request()
    request = StructuredRequest.from_dict(parsed)
    config = load_config()
    card = load_card(config["paths"]["cards"] / "hotel_es.yaml", config["freshness_days"])
    engine = Funnel(config, RecordedAdapters(None, None, FakeModel(parsed)), now=NOW)
    booking_candidate = Candidate.from_place(operational_place(primary_type="hotel"))
    booking_candidate.raw["booking"] = booking_row()
    places_candidate = Candidate.from_place(operational_place(primary_type="hotel"))

    booking_ledger = engine._build_ledger(booking_candidate, request, card, {}, {}, None, None)
    places_ledger = engine._build_ledger(places_candidate, request, card, {}, {}, None, None)

    assert "hours_at_arrival" not in {claim.claim_id for claim in booking_ledger.claims}
    assert "hours_at_arrival" in {claim.claim_id for claim in places_ledger.claims}


def test_booking_price_evidence_text_is_computed_from_the_stay(tmp_path) -> None:
    class CapturingModel(FakeModel):
        def __init__(self, parsed: dict) -> None:
            super().__init__(parsed)
            self.price_evidence = None

        def run(self, slot: str, payload: dict) -> dict:
            if slot == "judge":
                self.price_evidence = next(
                    row for row in payload["ledger"]["evidence"] if row["evidence_id"] == "booking_rate"
                )
            return super().run(slot, payload)

    parsed = hotel_request()
    resolved = operational_place(name="Hotel Uno", primary_type="hotel")
    places = {"resolved": {"Hotel Uno": resolved}, "details": {"p1": operational_details()}}
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    model = CapturingModel(parsed)
    Funnel(
        config,
        RecordedAdapters(FakePlaces(places), FakeWeb({}), model, booking=FakeBooking([booking_row()])),
        now=NOW,
    ).run({"ask": parsed["ask"], "card": "hotel", "depth": "full"})

    assert model.price_evidence["text"] == (
        "total EUR 740 for 2 nights, 2 adults, per Booking.com, read today; "
        "free cancellation: yes; breakfast: no"
    )
    assert model.price_evidence["source_kind"] == "booking_rate"
    assert model.price_evidence["source_class"] == "places"
    assert model.price_evidence["tier"] == 1


def test_missing_stay_refuses_without_calling_booking(tmp_path) -> None:
    parsed = hotel_request(stay=False)
    booking = FakeBooking([])

    output = run_hotel(tmp_path, parsed, booking, {})

    assert output["refusal"] is True
    assert output["reason"] == "stay_dates_missing"
    assert booking.sweep_calls == []


def test_room_level_attribute_needs_official_page_not_property_facilities(tmp_path) -> None:
    required = [{"claim_id": "suite_hot_tub", "claim_type": "layout", "text": "A hot tub is in the suite"}]
    parsed = hotel_request(required)
    resolved = operational_place(name="Hotel Uno", primary_type="hotel")
    details = operational_details()
    details["en"]["websiteUri"] = "https://hotel.example/"
    details["local"]["websiteUri"] = "https://hotel.example/"
    places = {"resolved": {"Hotel Uno": resolved}, "details": {"p1": details}}
    booking = FakeBooking([booking_row()], {"facilities": ["Hot tub", "Spa"]})

    facilities_only = run_hotel(tmp_path, parsed, booking, places)
    claim = next(row for row in facilities_only["candidates"][0]["claims"] if row["claim_id"] == "suite_hot_tub")
    assert claim["status"] == "unknown"

    official = {
        "official_pages": {
            "p1": {
                "pages": [{
                    "claim_id": "suite_hot_tub",
                    "url": "https://hotel.example/suites",
                    "text": "The corner suite has a private hot tub.",
                    "quote": "The corner suite has a private hot tub.",
                    "source_kind": "official",
                    "identity_label": "exact-venue",
                    "identity_reasons": ["official-domain"],
                }],
                "evidence": [],
            }
        }
    }
    supported = run_hotel(tmp_path, parsed, booking, places, official)
    claim = next(row for row in supported["candidates"][0]["claims"] if row["claim_id"] == "suite_hot_tub")
    assert claim["status"] == "supported"


def test_hotel_card_validates_and_unknown_sweep_source_fails() -> None:
    config = load_config()
    card = load_card(config["paths"]["cards"] / "hotel_es.yaml", config["freshness_days"])
    assert card["sweep_source"] == "booking"

    invalid = deepcopy(card)
    invalid["sweep_source"] = "other"
    with pytest.raises(CardValidationError, match="sweep_source"):
        validate_card(invalid, config["freshness_days"])
