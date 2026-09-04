import json
from datetime import datetime, timezone

import pytest

from osusume.adapters import AdapterError, GoplacesAdapter, RecordedAdapters, ReplayStore, SnapshotRecorder, WebAdapter, _identity_label
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request


class StubResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


class StubHeaders(dict):
    def get_content_charset(self) -> str:
        return "utf-8"


class StubTextResponse:
    def __init__(self, url: str, text: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.url = url
        self.body = text.encode()
        self.headers = StubHeaders({"Content-Type": content_type})
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.body[:size]

    def geturl(self) -> str:
        return self.url


def test_details_records_one_hours_supplement_and_replay_skips_https(monkeypatch, tmp_path) -> None:
    adapter = GoplacesAdapter()
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(adapter, "_run", lambda args: {"id": "p1", "language": args[3]})
    https_requests = []

    def fake_urlopen(http_request, timeout):
        https_requests.append((http_request, timeout))
        return StubResponse(
            {
                "regularOpeningHours": {
                    "periods": [
                        {
                            "open": {"day": 3, "hour": 9, "minute": 0},
                            "close": {"day": 3, "hour": 20, "minute": 0},
                        }
                    ]
                },
                "utcOffsetMinutes": 120,
            }
        )

    monkeypatch.setattr("osusume.adapters.urlopen", fake_urlopen)
    recorder = SnapshotRecorder(tmp_path)
    adapters = RecordedAdapters(adapter, None, None, recorder=recorder)
    call_request = {"place_id": "p1", "local_language": "it"}
    result = adapters.call("goplaces", "details", call_request, lambda: adapter.details("p1", "it"))
    recorder.finish({"ask": "x"}, {"ok": True})

    assert len(https_requests) == 1
    http_request, timeout = https_requests[0]
    assert http_request.full_url == (
        "https://places.googleapis.com/v1/places/p1"
        "?fields=regularOpeningHours,currentOpeningHours,utcOffsetMinutes"
    )
    assert dict((key.lower(), value) for key, value in http_request.header_items())["x-goog-api-key"] == "test-key"
    assert timeout == 30
    assert result["hours_supplement"]["utcOffsetMinutes"] == 120
    saved = json.loads(next((tmp_path / "raw").glob("*.json")).read_text())
    assert saved["response"]["hours_supplement"] == result["hours_supplement"]

    replay = RecordedAdapters(None, None, None, replay=ReplayStore(tmp_path))
    replayed = replay.call("goplaces", "details", call_request, lambda: pytest.fail("replay made a live call"))
    assert replayed == result
    assert len(https_requests) == 1


def test_details_omits_hours_when_supplement_fails(monkeypatch) -> None:
    adapter = GoplacesAdapter()
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(adapter, "_run", lambda args: {"id": "p1"})
    def failed_urlopen(http_request, timeout):
        raise OSError("down")

    monkeypatch.setattr("osusume.adapters.urlopen", failed_urlopen)

    assert "hours_supplement" not in adapter.details("p1", "it")


def test_official_pages_fetches_home_and_only_first_menu_link(monkeypatch) -> None:
    adapter = WebAdapter("https://example.test", retrieval={"max_pages_per_run": 0})
    calls = []
    responses = {
        "https://venue.example/": StubTextResponse(
            "https://venue.example/",
            """<html><head><title>Venue</title></head><body>
            <p>Welcome</p><a href="/carta">Carta</a><a href="/cocktails">Cocktails</a>
            </body></html>""",
        ),
        "https://venue.example/carta": StubTextResponse(
            "https://venue.example/carta",
            "<html><head><title>Carta</title></head><body>Negroni and martini</body></html>",
        ),
    }

    def fake_urlopen(http_request, timeout):
        calls.append((http_request.full_url, timeout))
        return responses[http_request.full_url]

    monkeypatch.setattr("osusume.adapters.urlopen", fake_urlopen)
    result = adapter.official_pages({"place_id": "p1", "name": "Venue"}, {"en": {"websiteUri": "https://venue.example/"}})

    assert calls == [("https://venue.example/", 15), ("https://venue.example/carta", 15)]
    assert [page["title"] for page in result["pages"]] == ["Venue", "Carta"]
    assert all(page["claim_id"] == "product_inventory" for page in result["pages"])
    assert all(page["source_kind"] == "official" for page in result["pages"])
    assert all(response.read_sizes == [512 * 1024] for response in responses.values())
    assert result["evidence"] == []
    assert result["budget_exhausted"] is False
    assert adapter._pages_retrieved == 0


def test_official_pages_marks_linked_instagram_as_exact_official_social(monkeypatch) -> None:
    adapter = WebAdapter("https://example.test")
    responses = {
        "https://venue.example/": StubTextResponse(
            "https://venue.example/",
            '<html><body><a href="https://instagram.com/venue">Instagram</a></body></html>',
        ),
        "https://instagram.com/venue": StubTextResponse(
            "https://instagram.com/venue",
            "<html><body>Open daily, cocktails served</body></html>",
        ),
    }
    monkeypatch.setattr("osusume.adapters.urlopen", lambda http_request, timeout: responses[http_request.full_url])

    result = adapter.official_pages(
        {"place_id": "p1", "name": "Venue"},
        {"en": {"websiteUri": "https://venue.example/"}},
    )

    social = [page for page in result["pages"] if page["source_kind"] == "official_social"]
    assert [page["claim_id"] for page in social] == ["product_inventory", "hours_at_arrival"]
    assert all(page["identity_label"] == "exact-venue" for page in social)
    assert all(page["identity_reasons"] == ["official-link"] for page in social)


def test_sweep_records_bad_type_and_keeps_good_type_candidates(monkeypatch, tmp_path) -> None:
    adapter = GoplacesAdapter()

    def fake_run(args):
        place_type = args[args.index("--type") + 1]
        if place_type == "point_of_interest":
            raise AdapterError("invalid type")
        return {"places": [operational_place()]}

    monkeypatch.setattr(adapter, "_run", fake_run)
    recorder = SnapshotRecorder(tmp_path)
    adapters = RecordedAdapters(adapter, None, None, recorder=recorder)
    raw_request = request()
    card = {"languages": {"en": []}, "places_types": ["point_of_interest", "food_store"]}
    call_request = {"request": raw_request, "card": card}
    result = adapters.call("goplaces", "sweep", call_request, lambda: adapter.sweep(raw_request, card))

    assert [row["id"] for row in result["candidates"]] == ["p1"]
    assert result["type_attempt_count"] == 2
    assert result["type_success_count"] == 1
    assert result["raw_calls"][0]["error"] == "invalid type"
    saved = json.loads(next((tmp_path / "raw").glob("*.json")).read_text())
    assert saved["response"]["raw_calls"][0]["error"] == "invalid type"


def test_route_sweep_flattens_waypoints_and_deduplicates_places_across_calls(monkeypatch) -> None:
    adapter = GoplacesAdapter()
    route_payloads = iter(
        [
            {
                "waypoints": [
                    {"location": {"latitude": 42.0, "longitude": 12.0}, "results": [{"place_id": "p1"}]},
                    {"location": {"latitude": 42.1, "longitude": 12.1}, "results": [{"place_id": "p2"}]},
                ]
            },
            {
                "waypoints": [
                    {"location": {"latitude": 42.0, "longitude": 12.0}, "results": [{"place_id": "p2"}]},
                    {"location": {"latitude": 42.1, "longitude": 12.1}, "results": [{"place_id": "p3"}]},
                ]
            },
        ]
    )
    monkeypatch.setattr(adapter, "_run", lambda args: next(route_payloads))

    result = adapter.sweep(request(route=True), {"languages": {"en": ["ceramics", "pottery"]}})

    assert [row["place_id"] for row in result["candidates"]] == ["p1", "p2", "p3"]
    assert len(result["raw_calls"]) == 2


def test_directions_uses_place_id_flags_only_for_place_id_endpoints(monkeypatch) -> None:
    adapter = GoplacesAdapter()
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"duration": "60s"})

    adapter.directions("Montpellier, France", "ChIJQSHXPwDnuhIRZ_YkVMyGp2Q", end_is_place_id=True)
    adapter.directions("ChIJQSHXPwDnuhIRZ_YkVMyGp2Q", "Barcelona, Spain", start_is_place_id=True)
    adapter.directions("43.611,3.877", "41.388,2.170")
    adapter.directions("anchor", "candidate", start_is_place_id=True, end_is_place_id=True, mode="walk")

    assert commands == [
        [
            "directions",
            "--from",
            "Montpellier, France",
            "--to-place-id",
            "ChIJQSHXPwDnuhIRZ_YkVMyGp2Q",
            "--mode",
            "drive",
        ],
        [
            "directions",
            "--from-place-id",
            "ChIJQSHXPwDnuhIRZ_YkVMyGp2Q",
            "--to",
            "Barcelona, Spain",
            "--mode",
            "drive",
        ],
        ["directions", "--from", "43.611,3.877", "--to", "41.388,2.170", "--mode", "drive"],
        ["directions", "--from-place-id", "anchor", "--to-place-id", "candidate", "--mode", "walk"],
    ]


def test_anchor_sweep_and_resolve_use_budget_radius(monkeypatch) -> None:
    adapter = GoplacesAdapter()
    commands = []
    monkeypatch.setattr(adapter, "_run", lambda args: commands.append(args) or {"places": []})
    anchor_request = {
        **request(),
        "scope": {
            "kind": "anchor",
            "place": "Anchor Bistro, Barcelona",
            "place_id": "anchor-bistro",
            "lat": 41.393,
            "lng": 2.155,
            "mode": "walk",
            "max_min": 8,
        },
    }

    adapter.sweep(anchor_request, {"languages": {"en": ["cocktail bar"]}, "places_types": ["bar"]})
    adapter.resolve("Injected Bar", anchor_request)

    assert all(command[command.index("--radius-m") + 1] == "640" for command in commands)
    assert commands[0][:2] == ["search", "cocktail bar"]
    assert commands[1][:3] == ["nearby", "--type", "bar"]
    assert commands[2][:4] == ["search", "Injected Bar", "--limit", "1"]


def test_stage1_fails_when_every_configured_type_failed() -> None:
    class FailedPlaces:
        def sweep(self, raw_request, card):
            return {"candidates": [], "raw_calls": [], "type_attempt_count": 2, "type_success_count": 0}

    engine = Funnel({}, RecordedAdapters(FailedPlaces(), None, None))
    with pytest.raises(AdapterError, match="every configured Places type failed"):
        engine.stage1_sweep(request=StructuredRequest.from_dict(request()), card={})


@pytest.mark.parametrize(
    ("source", "weight", "domains", "result", "expected"),
    [
        (
            "diffords_guide",
            1.0,
            ["diffordsguide.com"],
            {
                "url": "https://www.diffordsguide.com/bars/example-bar",
                "title": "Example Bar",
                "text": "One of the best cocktail bars in the city.",
            },
            "rated_entry",
        ),
        (
            "timeout_barcelona",
            1.0,
            ["timeout.com"],
            {
                "url": "https://www.timeout.com/barcelona/bars/best-cocktail-bars",
                "title": "The 10 best cocktail bars in Barcelona",
                "text": "Example Bar is one of our picks.",
            },
            "mention",
        ),
        (
            "tripadvisor",
            0.1,
            ["tripadvisor.com"],
            {"url": "https://www.tripadvisor.com/Example_Bar", "title": "Example Bar", "text": "Reviews"},
            "mention",
        ),
        (
            "diffords_guide",
            1.0,
            ["diffordsguide.com"],
            {"url": "https://other.example/venue", "title": "Example Bar", "text": "Review"},
            "mention",
        ),
        (
            "guide",
            1.0,
            [],
            {
                "url": "https://other.example/roundup",
                "title": "10 Best Bars: Example Bar",
                "text": "Review",
                "entry_type": "rated_entry",
                "rating": 9.5,
            },
            "rated_entry",
        ),
    ],
)
def test_registry_classifies_guide_results(monkeypatch, source, weight, domains, result, expected) -> None:
    adapter = WebAdapter("https://example.test", api_key="test-key")
    monkeypatch.setattr(adapter, "_search", lambda query: {"results": [result]})
    card = {"sources": {"ES": {source: weight}}, "source_domains": {source: domains}}

    output = adapter.registry(
        {"country": "ES", "category": "cocktail_bar", "ask": "Barcelona"},
        card,
        [{"place_id": "p1", "name": "Example Bar"}],
    )

    assert output["qualifications"][0]["entry_type"] == expected


def test_registry_query_appends_candidate_locality(monkeypatch) -> None:
    adapter = WebAdapter("https://example.test", api_key="test-key")
    queries = []
    monkeypatch.setattr(adapter, "_search", lambda query: queries.append(query) or {"results": []})

    adapter.registry(
        {"country": "ES", "category": "cocktail_bar", "ask": "a quiet bar"},
        {"sources": {"ES": {"local_guide": 1.0}}},
        [{"place_id": "p1", "name": "Casa Uno", "formattedAddress": "1 Main Street, Barcelona, Spain"}],
    )

    assert queries == ["local guide cocktail_bar a quiet bar Barcelona"]


def test_mined_pages_keep_claim_target_and_reach_normal_ledger_compute(monkeypatch, tmp_path) -> None:
    class LiteralPageModel(FakeModel):
        def run(self, slot: str, payload: dict) -> dict:
            response = super().run(slot, payload)
            if slot == "judge":
                evidence_by_id = {row["evidence_id"]: row for row in payload["ledger"]["evidence"]}
                for judgment in response["judgments"]:
                    if not judgment["quote"]:
                        judgment["quote"] = evidence_by_id[judgment["evidence_id"]]["text"]
            return response

    adapter = WebAdapter("https://example.test", api_key="test-key")
    monkeypatch.setattr(
        adapter,
        "_search",
        lambda query: {
            "retrieved_at": "2026-08-26T10:00:00+00:00",
            "results": [
                {
                    "url": "https://venue.example/catalog",
                    "title": "Catalog",
                    "text": "Handmade ceramics are available in our shop.",
                    "publishedDate": "2026-08-20T00:00:00+00:00",
                    "source_kind": "official_site",
                }
            ],
        },
    )
    required = {"claim_id": "sells_ceramics", "claim_type": "product_inventory", "text": "sells handmade ceramics"}
    parsed = request([required])
    card = {
        "query_templates": [
            {"template": "{name} workshop", "claim_id": "counter_service"},
            "{name} reviews",
        ],
        "load_bearing_claims": ["operational_status", "hours_at_arrival", "detour", "product_inventory", "counter_service", "quality"],
    }
    mined = adapter.mine(
        {"place_id": "p1", "name": "Test Place", "details": {"en": {"websiteUri": "https://venue.example/"}}},
        parsed,
        card,
    )

    assert [page["claim_id"] for page in mined["pages"]] == ["counter_service", "quality", "sells_ceramics"]

    attribute_page = mined["pages"][-1]
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [attribute_page], "evidence": []}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(
        config,
        RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), LiteralPageModel(parsed)),
        now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc),
    ).run({"ask": parsed["ask"], "card": "salumeria", "depth": "full"})

    claim = next(row for row in output["candidates"][0]["claims"] if row["claim_id"] == "sells_ceramics")
    assert claim["status"] == "supported"
    assert claim["qualified_evidence_ids"] == ["web_0"]


def test_mined_pages_are_bound_to_candidate_identity_and_queries_use_local_details(monkeypatch) -> None:
    adapter = WebAdapter("https://example.test", api_key="test-key")
    queries = []
    monkeypatch.setattr(
        adapter,
        "_search",
        lambda query: queries.append(query) or {
            "retrieved_at": "2026-08-26T10:00:00+00:00",
            "results": [
                {
                    "url": "https://press.example/venue",
                    "title": "A review",
                    "text": "Call +34 612 345 678 for reservations.",
                    "publishedDate": "2026-08-20T00:00:00+00:00",
                    "source_kind": "local_press",
                },
                {
                    "url": "https://directory.example/madrid",
                    "title": "Casa Uno",
                    "text": "Casa Uno in Madrid",
                    "address": "99 Other Street, Madrid, Spain",
                },
                {
                    "url": "https://guide.example/barcelona",
                    "title": "Casa Uno",
                    "text": "Casa Uno is popular in Barcelona.",
                },
                {
                    "url": "https://casauno.example/menu",
                    "title": "Menu",
                    "text": "Today’s menu",
                },
            ],
        },
    )
    details = {
        "en": {
            "id": "ChIJ-casa-uno",
            "displayName": {"text": "Casa One"},
            "formattedAddress": "1 Main Street, Barcelona, Spain",
            "nationalPhoneNumber": "+34 612 345 678",
            "websiteUri": "https://casauno.example/",
            "location": {"latitude": 41.39, "longitude": 2.17},
            "businessStatus": "OPERATIONAL",
        },
        "local": {"displayName": {"text": "Casa Uno"}},
    }
    candidate_row = {"place_id": "ChIJ-casa-uno", "name": "Casa One", "details": details}
    parsed = {**request(), "scope": {"kind": "near"}, "arrival_start": None, "arrival_end": None}
    card = {"query_templates": [{"template": "{name} {city}", "claim_id": "quality"}], "load_bearing_claims": ["quality"]}

    mined = adapter.mine(candidate_row, parsed, card)

    assert queries == ["Casa One Barcelona Casa Uno"]
    assert [page["identity_label"] for page in mined["pages"]] == [
        "exact-venue",
        "ambiguous",
        "area-level",
        "exact-venue",
    ]
    candidate = Candidate.from_place(operational_place(place_id="ChIJ-casa-uno", name="Casa One"))
    candidate.details = details["en"]
    engine = Funnel(load_config(), RecordedAdapters(None, None, None), now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc))
    ledger = engine._build_ledger(candidate, StructuredRequest.from_dict(parsed), card, mined, candidate.details, None, None)
    web_rows = [row for row in ledger.evidence if row.evidence_id.startswith("web_")]
    assert [row.metadata["identity_label"] for row in web_rows] == ["exact-venue", "exact-venue"]
    ledger.compute(
        [
            {"claim_id": row.claim_id, "evidence_id": row.evidence_id, "quote": row.text, "entails": True}
            for row in ledger.evidence
        ],
        load_config()["freshness_days"],
        now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc),
    )
    quality = next(claim for claim in ledger.claims if claim.claim_id == "quality")
    assert quality.status.value == "supported"
    assert "exact-venue" in quality.evidence_clause


def test_build_ledger_passes_nested_candidate_details_to_identity_check() -> None:
    parsed = {**request(), "scope": {"kind": "near"}, "arrival_start": None, "arrival_end": None}
    candidate = Candidate.from_place(operational_place(place_id="ChIJ-venue", name="Venue"))
    candidate.details = {
        "en": {
            "websiteUri": "https://venue.example/",
            "nationalPhoneNumber": "+34 612 345 678",
        },
        "local": {},
        "hours_supplement": {},
    }
    mined = {
        "pages": [
            {
                "claim_id": "quality",
                "url": "https://venue.example/drinks",
                "title": "Drinks",
                "text": "Reserve at +34 612 345 678.",
                "retrieved_at": "2026-08-26T10:00:00+00:00",
            }
        ]
    }
    engine = Funnel(load_config(), RecordedAdapters(None, None, None), now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc))

    ledger = engine._build_ledger(
        candidate,
        StructuredRequest.from_dict(parsed),
        {"load_bearing_claims": ["quality"]},
        mined,
        {},
        None,
        None,
    )

    page = next(row for row in ledger.evidence if row.evidence_id == "web_0")
    assert page.metadata["identity_label"] == "exact-venue"
    assert page.metadata["identity_reasons"] == ["phone", "official-domain"]


def test_identity_phone_match_beats_an_unrelated_second_phone() -> None:
    page = {"url": "https://press.example/venue", "text": "Venue: +34 612 345 678. Taxi: +34 934 567 890."}
    candidate = {"place_id": "ChIJ-venue", "details": {"nationalPhoneNumber": "+34 612 345 678"}}

    assert _identity_label(page, candidate) == ("exact-venue", ["phone"])


def test_identity_different_place_id_overrides_phone_match() -> None:
    page = {
        "url": "https://press.example/venue",
        "text": "Venue: +34 612 345 678. Map place_id=ChIJ-other-venue",
    }
    candidate = {"place_id": "ChIJ-venue", "details": {"nationalPhoneNumber": "+34 612 345 678"}}

    assert _identity_label(page, candidate) == ("ambiguous", ["phone"])


def test_identity_different_address_without_strong_signal_is_ambiguous() -> None:
    page = {"url": "https://press.example/venue", "address": "99 Other Street, Madrid, Spain"}
    candidate = {"place_id": "ChIJ-venue", "details": {"formattedAddress": "1 Main Street, Barcelona, Spain"}}

    assert _identity_label(page, candidate) == ("ambiguous", [])


def test_aggregator_website_is_not_official_but_phone_can_bind_listing(monkeypatch) -> None:
    adapter = WebAdapter("https://example.test", api_key="test-key")
    monkeypatch.setattr(
        adapter,
        "_search",
        lambda query: {
            "retrieved_at": "2026-08-26T10:00:00+00:00",
            "results": [
                {
                    "url": "https://www.privateaser.es/blog/top-bars",
                    "title": "Top bars",
                    "text": "The best bars in Barcelona.",
                },
                {
                    "url": "https://www.privateaser.es/local/venue",
                    "title": "Venue",
                    "text": "Reserve Venue at +34 612 345 678.",
                },
            ],
        },
    )
    details = {
        "en": {
            "websiteUri": "https://www.privateaser.es/local/venue",
            "nationalPhoneNumber": "+34 612 345 678",
        },
        "local": {},
    }
    parsed = {**request(), "scope": {"kind": "near"}, "arrival_start": None, "arrival_end": None}
    card = {"query_templates": [{"template": "{name} bars", "claim_id": "quality"}], "load_bearing_claims": ["quality"]}
    candidate_row = {"place_id": "ChIJ-venue", "name": "Venue", "details": details}

    mined = adapter.mine(candidate_row, parsed, card)

    assert [(page["identity_label"], page["identity_reasons"]) for page in mined["pages"]] == [
        ("ambiguous", []),
        ("exact-venue", ["phone"]),
    ]
    candidate = Candidate.from_place(operational_place(place_id="ChIJ-venue", name="Venue"))
    candidate.details = details
    engine = Funnel(load_config(), RecordedAdapters(None, None, None), now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc))
    ledger = engine._build_ledger(candidate, StructuredRequest.from_dict(parsed), card, mined, {}, None, None)
    page = next(row for row in ledger.evidence if row.evidence_id == "web_0")
    assert page.source_kind == "generic_web"


def test_mine_stops_at_query_budget(monkeypatch) -> None:
    adapter = WebAdapter(
        "https://example.test",
        api_key="test-key",
        retrieval={"max_queries_per_candidate": 2, "max_results_per_query": 5, "max_pages_per_run": 60},
    )
    searches = []
    monkeypatch.setattr(adapter, "_search", lambda query: searches.append(query) or {"results": []})
    card = {"query_templates": ["{name} one", "{name} two", "{name} three"]}

    mined = adapter.mine({"place_id": "p1", "name": "Test Place"}, request(), card)

    assert searches == ["Test Place one", "Test Place two"]
    assert mined["budget_exhausted"] is True


def test_budget_exhaustion_is_shown_in_human_output(tmp_path) -> None:
    parsed = request()
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": [], "budget_exhausted": True}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path

    output = Funnel(
        config,
        RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)),
        now=datetime(2026, 8, 26, 10, tzinfo=timezone.utc),
    ).run({"ask": parsed["ask"], "card": "salumeria", "depth": "full"})

    assert output["budget_exhausted"] is True
    assert "Search budget reached; evidence may be incomplete." in output["human"].splitlines()
