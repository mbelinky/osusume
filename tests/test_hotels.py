from copy import deepcopy
from datetime import datetime, timezone

import pytest

from osusume.adapters import (
    AdapterError,
    BookingAdapter,
    GoplacesAdapter,
    RecordedAdapters,
    ReplayStore,
    SnapshotRecorder,
    WebAdapter,
)
from osusume.cards import CardValidationError, load_card, validate_card
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place


NOW = datetime(2026, 9, 4, 10, tzinfo=timezone.utc)


class StubHeaders(dict):
    def get_content_charset(self) -> str:
        return "utf-8"


class StubTextResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.body = text.encode()
        self.headers = StubHeaders({"Content-Type": "text/html; charset=utf-8"})

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body[:size]

    def geturl(self) -> str:
        return self.url


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

    def sweep(self, request: dict, card: dict, offset: int = 0) -> dict:
        self.sweep_calls.append((deepcopy(request), deepcopy(card), offset))
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
        "hot_tub": True,
    }

    result = adapter.sweep(request, {"sweep_source": "booking"})

    assert commands == [[
        "hotels", "list", "--query", "Plaça de Catalunya", "--checkin", "2026-10-01",
        "--checkout", "2026-10-03", "--adults", "2", "--currency", "EUR", "--nflt",
        "class=4;class=5;review_score=80;hotelfacility=4;hotelfacility=54;mealplan=1;fc=2", "--order",
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


def test_booking_adapter_rejects_missing_city() -> None:
    request = hotel_request()
    request["scope"].pop("city")

    with pytest.raises(AdapterError, match="city_missing"):
        BookingAdapter("booking-test").sweep(request, {})


def test_booking_adapter_adds_offset_after_first_page(monkeypatch) -> None:
    adapter = BookingAdapter("booking-test")
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"results": []})

    adapter.sweep(hotel_request(), {"sweep_source": "booking"}, 25)

    assert commands[0][-2:] == ["--offset", "25"]


@pytest.mark.parametrize(
    ("filter_name", "filter_code"),
    [
        ("hot_tub", "hotelfacility=54"),
        ("pets", "hotelfacility=4"),
        ("breakfast", "mealplan=1"),
        ("free_cancellation", "fc=2"),
    ],
)
def test_booking_facility_filters_run_filtered_and_broad_commands(
    monkeypatch, filter_name: str, filter_code: str
) -> None:
    adapter = BookingAdapter("booking-test")
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"results": [booking_row()]})
    request = hotel_request()
    request["hotel_filters"] = {"min_stars": 4, "max_stars": 4, "min_score": 8, filter_name: True}
    engine = Funnel(load_config(), RecordedAdapters(None, None, None, booking=adapter), now=NOW)

    result = engine._booking_sweep(StructuredRequest.from_dict(request), {"sweep_source": "booking"})

    assert len(result["candidates"]) == 1
    assert len(commands) == 2
    assert commands[0][commands[0].index("--nflt") + 1] == f"class=4;review_score=80;{filter_code}"
    assert commands[1][commands[1].index("--nflt") + 1] == "class=4;review_score=80"
    assert all("--offset" not in command for command in commands)


def test_booking_star_filter_runs_one_command_for_a_full_result_page(monkeypatch) -> None:
    adapter = BookingAdapter("booking-test")
    commands = []
    rows = [booking_row(f"Hotel {index}", f"hotel-{index}") for index in range(25)]
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"results": rows})
    request = hotel_request()
    request["hotel_filters"] = {"min_stars": 4}
    engine = Funnel(load_config(), RecordedAdapters(None, None, None, booking=adapter), now=NOW)

    result = engine._booking_sweep(StructuredRequest.from_dict(request), {"sweep_source": "booking"})

    assert len(result["candidates"]) == 25
    assert len(commands) == 1
    assert commands[0][commands[0].index("--nflt") + 1] == "class=4;class=5"
    assert "--offset" not in commands[0]


def test_booking_queries_merge_dedupe_cap_record_and_replay(tmp_path) -> None:
    class FilterAwareBooking:
        def __init__(self) -> None:
            self.calls = []

        def sweep(self, request: dict, card: dict, offset: int = 0) -> dict:
            self.calls.append((deepcopy(request), offset))
            if request["hotel_filters"].get("hot_tub"):
                rows = [booking_row("Filtered", "filtered"), booking_row("Shared", "shared")]
            else:
                rows = [
                    {**booking_row("Shared", "shared"), "price": 999},
                    booking_row("Broad", "broad"),
                    booking_row("Capped", "capped"),
                ]
            return {"candidates": [{"name": row["name"], "raw": {"booking": row}} for row in rows]}

    request = hotel_request()
    request["hotel_filters"] = {"min_stars": 4, "hot_tub": True}
    booking = FilterAwareBooking()
    config = load_config()
    config["retrieval"]["booking_max_rows"] = 3
    recorder = SnapshotRecorder(tmp_path)
    engine = Funnel(config, RecordedAdapters(None, None, None, booking=booking, recorder=recorder), now=NOW)

    result = engine._booking_sweep(StructuredRequest.from_dict(request), {"sweep_source": "booking"})

    assert [row["raw"]["booking"]["slug"] for row in result["candidates"]] == ["filtered", "shared", "broad"]
    assert result["candidates"][1]["raw"]["booking"]["price"] == 740
    assert [offset for _, offset in booking.calls] == [0, 0]
    assert booking.calls[0][0]["hotel_filters"] == {"min_stars": 4, "hot_tub": True}
    assert booking.calls[1][0]["hotel_filters"] == {"min_stars": 4}
    assert [call["request"]["offset"] for call in recorder.calls] == [0, 0]
    assert [call["request"]["request"]["hotel_filters"] for call in recorder.calls] == [
        {"min_stars": 4, "hot_tub": True},
        {"min_stars": 4},
    ]

    recorder.finish({}, {})
    replay = ReplayStore(tmp_path)
    replay_engine = Funnel(config, RecordedAdapters(None, None, None, replay=replay), now=NOW)
    replayed = replay_engine._booking_sweep(StructuredRequest.from_dict(request), {"sweep_source": "booking"})

    assert replayed == result
    assert replay.index == 2


def test_booking_broad_query_runs_when_filtered_rows_fill_the_cap() -> None:
    class FullQueries:
        def __init__(self) -> None:
            self.filters = []

        def sweep(self, request: dict, card: dict, offset: int = 0) -> dict:
            self.filters.append(deepcopy(request["hotel_filters"]))
            prefix = "filtered" if request["hotel_filters"].get("hot_tub") else "broad"
            rows = [booking_row(f"Hotel {prefix} {index}", f"{prefix}-{index}") for index in range(25)]
            return {"candidates": [{"name": row["name"], "raw": {"booking": row}} for row in rows]}

    request = hotel_request()
    request["hotel_filters"] = {"hot_tub": True}
    booking = FullQueries()
    config = load_config()
    config["retrieval"]["booking_max_rows"] = 10
    engine = Funnel(config, RecordedAdapters(None, None, None, booking=booking), now=NOW)

    result = engine._booking_sweep(StructuredRequest.from_dict(request), {"sweep_source": "booking"})

    assert booking.filters == [{"hot_tub": True}, {}]
    assert len(result["candidates"]) == 10
    assert all(row["raw"]["booking"]["slug"].startswith("filtered-") for row in result["candidates"])


def test_near_booking_sweep_without_city_refuses_closed(tmp_path) -> None:
    parsed = hotel_request()
    parsed["scope"].pop("city")
    booking = FakeBooking([])

    output = run_hotel(tmp_path, parsed, booking, {})

    assert output["refusal"] is True
    assert output["reason"] == "city_missing"
    assert booking.sweep_calls == []


@pytest.mark.parametrize(
    "phrase",
    ["hot tub", "jacuzzi", "whirlpool", "spa bath", "bañera de hidromasaje", "jacuzzi privado"],
)
def test_hot_tub_phrases_set_parse_filter(tmp_path, phrase: str) -> None:
    parsed = hotel_request()
    parsed["ask"] = f"Hotel in Barcelona with {phrase}"
    parsed["hotel_filters"] = {}
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    engine = Funnel(config, RecordedAdapters(None, None, FakeModel(parsed)), now=NOW)

    request, _ = engine.stage0_parse({"ask": parsed["ask"], "card": "hotel"})

    assert request.hotel_filters["hot_tub"] is True


def test_cli_city_overrides_city_parsed_from_ask(tmp_path) -> None:
    parsed = hotel_request()
    parsed["scope"]["city"] = "Madrid"
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    engine = Funnel(config, RecordedAdapters(None, None, FakeModel(parsed)), now=NOW)

    request, _ = engine.stage0_parse(
        {
            "ask": parsed["ask"],
            "card": "hotel",
            "scope": {"kind": "near", "city": "Barcelona", "lat": 42.0, "lng": 12.0},
        }
    )

    assert request.scope["city"] == "Barcelona"


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


def test_unfiltered_only_hot_tub_hotel_keeps_the_same_mining_and_output(tmp_path) -> None:
    class PositionedBooking(FakeBooking):
        def __init__(self, *, appears_in_filtered_query: bool) -> None:
            super().__init__([booking_row()])
            self.appears_in_filtered_query = appears_in_filtered_query

        def sweep(self, request: dict, card: dict, offset: int = 0) -> dict:
            self.sweep_calls.append((deepcopy(request), deepcopy(card), offset))
            is_filtered_query = request["hotel_filters"].get("hot_tub") is True
            rows = self.rows if is_filtered_query == self.appears_in_filtered_query else []
            return {"candidates": [{"name": row["name"], "raw": {"booking": deepcopy(row)}} for row in rows]}

    required = [{"claim_id": "hotel_hot_tub", "claim_type": "layout", "text": "The hotel has a hot tub"}]
    parsed = hotel_request(required)
    parsed["ask"] = "hotel in Barcelona with hot tub"
    parsed["hotel_filters"] = {"hot_tub": True}
    resolved = operational_place(name="Hotel Uno", primary_type="hotel")
    details = operational_details()
    details["en"]["websiteUri"] = "https://hotel.example/"
    details["local"]["websiteUri"] = "https://hotel.example/"
    places = {"resolved": {"Hotel Uno": resolved}, "details": {"p1": details}}
    web = {
        "official_pages": {
            "p1": {
                "pages": [{
                    "claim_id": "hotel_hot_tub",
                    "url": "https://hotel.example/spa",
                    "text": "The hotel spa includes a hot tub.",
                    "quote": "The hotel spa includes a hot tub.",
                    "source_kind": "official",
                    "identity_label": "exact-venue",
                    "identity_reasons": ["official-domain"],
                }],
                "evidence": [],
            }
        }
    }
    filtered_booking = PositionedBooking(appears_in_filtered_query=True)
    broad_booking = PositionedBooking(appears_in_filtered_query=False)

    filtered_output = run_hotel(tmp_path / "filtered", parsed, filtered_booking, places, web)
    broad_output = run_hotel(tmp_path / "broad", parsed, broad_booking, places, web)

    assert [call[0]["hotel_filters"] for call in filtered_booking.sweep_calls] == [{"hot_tub": True}, {}]
    assert [call[0]["hotel_filters"] for call in broad_booking.sweep_calls] == [{"hot_tub": True}, {}]
    assert parsed["hotel_filters"] == {"hot_tub": True}
    assert broad_output["candidates"] == filtered_output["candidates"]
    candidate = broad_output["candidates"][0]
    assert candidate["verdict"] == "cleared"
    assert candidate["reason"] is None
    assert next(claim for claim in candidate["claims"] if claim["claim_id"] == "hotel_hot_tub")["status"] == "supported"


@pytest.mark.parametrize(
    ("filters", "row_changes"),
    [
        ({"min_stars": 5}, {}),
        ({"max_stars": 3}, {}),
        ({"min_score": 9.5}, {}),
        ({"breakfast": True}, {}),
        ({"free_cancellation": True}, {"free_cancellation": False}),
    ],
)
def test_booking_reported_row_filter_mismatches_still_reject(
    tmp_path, filters: dict, row_changes: dict
) -> None:
    parsed = hotel_request()
    parsed["hotel_filters"] = filters
    row = {**booking_row(), **row_changes}
    places = {"resolved": {"Hotel Uno": operational_place(name="Hotel Uno", primary_type="hotel")}}

    output = run_hotel(tmp_path, parsed, FakeBooking([row]), places)

    assert output["candidates"][0]["reason"] == "hotel_filter_mismatch"


def test_booking_resolution_uses_lodging_type_and_rejects_non_lodging(tmp_path) -> None:
    class CapturingPlaces(FakePlaces):
        def __init__(self, fixture: dict) -> None:
            super().__init__(fixture)
            self.resolve_types = []

        def resolve(self, name: str, request: dict, place_type: str | None = None) -> dict | None:
            self.resolve_types.append(place_type)
            return super().resolve(name, request, place_type)

    parsed = hotel_request()
    lodging_by_types = operational_place(name="Hotel Uno", primary_type="hotel")
    lodging_by_types.pop("primaryType")
    lodging_by_types["types"] = ["hotel", "lodging", "point_of_interest"]
    non_lodging_by_types = operational_place("bar", "Hotel Bar", "bar")
    non_lodging_by_types.pop("primaryType")
    non_lodging_by_types["types"] = ["bar", "point_of_interest"]
    places = CapturingPlaces({
        "resolved": {
            "Hotel Bar": non_lodging_by_types,
            "Hotel Uno": lodging_by_types,
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


def test_terrace_official_home_and_rooms_pages_support_private_room_hot_tub(monkeypatch) -> None:
    homepage_sentence = "Todas las habitaciones disponen de bañera de hidromasaje."
    penthouse_sentence = "El Penthouse tiene una espectacular terraza de 90 m² con jacuzzi."
    responses = {
        "https://example-terrace-hotel.test/es/": StubTextResponse(
            "https://example-terrace-hotel.test/es/",
            f'<html><body><p>{homepage_sentence}</p><a href="/es/habitaciones/">Habitaciones</a></body></html>',
        ),
        "https://example-terrace-hotel.test/es/habitaciones/": StubTextResponse(
            "https://example-terrace-hotel.test/es/habitaciones/",
            f"<html><body><p>{penthouse_sentence}</p></body></html>",
        ),
    }
    monkeypatch.setattr("osusume.adapters.urlopen", lambda http_request, timeout: responses[http_request.full_url])
    config = load_config()
    card = load_card(config["paths"]["cards"] / "hotel_es.yaml", config["freshness_days"])
    details_payload = {
        "en": {
            "id": "terrace",
            "displayName": {"text": "Terrace Suites Hotel"},
            "businessStatus": "OPERATIONAL",
            "websiteUri": "https://example-terrace-hotel.test/es/",
        }
    }
    official = WebAdapter("https://example.test").official_pages(
        {"place_id": "terrace", "name": "Terrace Suites Hotel", "details": details_payload},
        details_payload,
        card,
    )
    required = {
        "claim_id": "private_hot_tub_in_room",
        "claim_type": "layout",
        "text": "A private hot tub is in the room",
        "synonyms": ["jacuzzi", "whirlpool", "spa bath", "hidromasaje", "bañera de hidromasaje"],
    }
    candidate = Candidate.from_place(operational_place("terrace", "Terrace Suites Hotel", "hotel"))
    candidate.details = details_payload["en"]
    parsed = StructuredRequest.from_dict(hotel_request([required], stay=False))

    class TerraceJudge:
        def __init__(self) -> None:
            self.payload = None

        def run(self, slot: str, payload: dict) -> dict:
            assert slot == "judge"
            self.payload = payload
            evidence = next(
                row
                for row in payload["ledger"]["evidence"]
                if row["claim_id"] == "private_hot_tub_in_room" and penthouse_sentence in row["text"]
            )
            return {
                "judgments": [{
                    "claim_id": "private_hot_tub_in_room",
                    "evidence_id": evidence["evidence_id"],
                    "quote": penthouse_sentence,
                    "entails": True,
                    "contradicts": False,
                }]
            }

    judge = TerraceJudge()
    engine = Funnel(config, RecordedAdapters(None, None, judge), now=NOW)
    candidate.ledger = engine._build_ledger(candidate, parsed, card, official, candidate.details, None, None)
    engine.stage5_judge([candidate], card)

    claim = next(row for row in candidate.ledger.claims if row.claim_id == "private_hot_tub_in_room")
    claim_evidence = [row for row in judge.payload["ledger"]["evidence"] if row["claim_id"] == claim.claim_id]
    assert [row["text"] for row in claim_evidence] == [homepage_sentence + " Habitaciones", penthouse_sentence]
    assert next(row for row in judge.payload["ledger"]["claims"] if row["claim_id"] == claim.claim_id)["synonyms"] == required["synonyms"]
    assert "shared spa or property-level amenity is insufficient" in judge.payload["instruction"]
    assert claim.status.value == "supported"
    assert claim.evidence_clause.startswith("official, exact-venue")


def test_hotel_card_validates_and_unknown_sweep_source_fails() -> None:
    config = load_config()
    card = load_card(config["paths"]["cards"] / "hotel_es.yaml", config["freshness_days"])
    assert card["sweep_source"] == "booking"

    invalid = deepcopy(card)
    invalid["sweep_source"] = "other"
    with pytest.raises(CardValidationError, match="sweep_source"):
        validate_card(invalid, config["freshness_days"])
