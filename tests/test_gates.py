import json
from copy import deepcopy
from datetime import datetime, timezone

from osusume.adapters import AdapterError, RecordedAdapters, ReplayStore, SnapshotRecorder, WebAdapter
from osusume.config import load_config
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel, HoursWindowStatus, open_for_window, true_detour_minutes
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request


NOW = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)


class LiteralPageModel(FakeModel):
    def __init__(self, parsed: dict) -> None:
        super().__init__(parsed)
        self.judge_payloads = []

    def run(self, slot: str, payload: dict) -> dict:
        response = super().run(slot, payload)
        if slot == "judge":
            self.judge_payloads.append(deepcopy(payload))
            evidence_by_id = {row["evidence_id"]: row for row in payload["ledger"]["evidence"]}
            for judgment in response["judgments"]:
                judgment["quote"] = evidence_by_id[judgment["evidence_id"]]["text"]
        return response


class QuickWeb(WebAdapter):
    def registry(self, request: dict, card: dict, candidates: list[dict] | None = None) -> dict:
        return {"qualifications": [], "injected": []}


class TextHeaders(dict):
    def get_content_charset(self) -> str:
        return "utf-8"


class TextResponse:
    def __init__(self, url: str, text: str) -> None:
        self.url = url
        self.body = text.encode()
        self.headers = TextHeaders({"Content-Type": "text/html; charset=utf-8"})

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.body[:size]

    def geturl(self) -> str:
        return self.url


def run_quick(tmp_path, monkeypatch, *, website: str | None, fetcher):
    parsed = request([{"claim_id": "product_inventory", "claim_type": "product_inventory", "text": "Craft cocktails are served"}])
    details = operational_details()
    if website:
        details["en"]["websiteUri"] = website
    fixture = {
        "sweep": [operational_place(primary_type="bar")],
        "details": {"p1": details},
    }
    monkeypatch.setattr("osusume.adapters.urlopen", fetcher)
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    model = LiteralPageModel(parsed)
    raw_input = {"ask": parsed["ask"], "card": "cocktail_bar", "depth": "quick"}
    output = Funnel(
        config,
        RecordedAdapters(FakePlaces(fixture), QuickWeb("https://example.test"), model),
        now=NOW,
    ).run(raw_input)
    return output, model, parsed, fixture, config, raw_input


def test_quick_run_supports_product_from_official_site_and_clears(monkeypatch, tmp_path) -> None:
    fetches = []

    def fetcher(http_request, timeout):
        fetches.append(http_request.full_url)
        return TextResponse(http_request.full_url, "<html><body>Our craft cocktail menu includes a house martini.</body></html>")

    output, model, _, _, _, _ = run_quick(
        tmp_path,
        monkeypatch,
        website="https://venue.example/",
        fetcher=fetcher,
    )

    candidate = output["candidates"][0]
    product = next(claim for claim in candidate["claims"] if claim["claim_id"] == "product_inventory")
    official = next(row for row in model.judge_payloads[0]["ledger"]["evidence"] if row["claim_id"] == "product_inventory")
    assert fetches == ["https://venue.example/"]
    assert product["status"] == "supported"
    assert official["source_kind"] == "official_site"
    assert official["source_class"] == "official"
    assert candidate["verdict"] == "cleared"


def test_quick_run_without_website_keeps_product_unknown(monkeypatch, tmp_path) -> None:
    def unexpected_fetch(http_request, timeout):
        raise AssertionError("no website should mean no fetch")

    output, _, _, _, _, _ = run_quick(tmp_path, monkeypatch, website=None, fetcher=unexpected_fetch)

    product = next(claim for claim in output["candidates"][0]["claims"] if claim["claim_id"] == "product_inventory")
    assert product["status"] == "unknown"
    assert output["candidates"][0]["verdict"] == "unconfirmed"


def test_quick_run_skips_failed_official_fetch(monkeypatch, tmp_path) -> None:
    def failed_fetch(http_request, timeout):
        raise OSError("site unavailable")

    output, _, _, _, _, _ = run_quick(
        tmp_path,
        monkeypatch,
        website="https://venue.example/",
        fetcher=failed_fetch,
    )

    product = next(claim for claim in output["candidates"][0]["claims"] if claim["claim_id"] == "product_inventory")
    assert product["status"] == "unknown"
    assert output["candidates"][0]["verdict"] == "unconfirmed"


def test_recorded_quick_run_replays_without_fetching_network(monkeypatch, tmp_path) -> None:
    parsed = request([{"claim_id": "product_inventory", "claim_type": "product_inventory", "text": "Craft cocktails are served"}])
    details = operational_details()
    details["en"]["websiteUri"] = "https://venue.example/"
    fixture = {"sweep": [operational_place(primary_type="bar")], "details": {"p1": details}}
    config = load_config()
    config["paths"]["drafts"] = tmp_path / "drafts"
    raw_input = {"ask": parsed["ask"], "card": "cocktail_bar", "depth": "quick"}
    recorder = SnapshotRecorder(tmp_path / "run")
    monkeypatch.setattr(
        "osusume.adapters.urlopen",
        lambda http_request, timeout: TextResponse(
            http_request.full_url,
            "<html><body>Our craft cocktail menu includes a house martini.</body></html>",
        ),
    )
    recorded = Funnel(
        config,
        RecordedAdapters(
            FakePlaces(fixture),
            QuickWeb("https://example.test"),
            LiteralPageModel(parsed),
            recorder=recorder,
        ),
        now=recorder.run_at,
    ).run(raw_input)
    recorder.finish(raw_input, recorded)

    def unexpected_fetch(http_request, timeout):
        raise AssertionError("replay touched the network")

    monkeypatch.setattr("osusume.adapters.urlopen", unexpected_fetch)
    replayed = Funnel(
        config,
        RecordedAdapters(None, None, None, replay=ReplayStore(tmp_path / "run")),
        now=recorder.run_at,
    ).run(raw_input)

    assert replayed == recorded


def test_full_depth_reads_official_pages_first_dedupes_search_and_reuses_details() -> None:
    parsed = request()
    details = operational_details()
    details["en"]["websiteUri"] = "https://venue.example/"
    fixture = {
        "details": {"p1": details},
        "official_pages": {
            "p1": {
                "pages": [
                    {
                        "claim_id": "product_inventory",
                        "url": "https://venue.example/",
                        "text": "Official home page",
                        "identity_label": "exact-venue",
                    },
                    {
                        "claim_id": "product_inventory",
                        "url": "https://venue.example/menu",
                        "text": "Official menu page",
                        "identity_label": "exact-venue",
                    },
                ],
                "evidence": [],
            }
        },
        "mined": {
            "p1": {
                "pages": [
                    {
                        "claim_id": "quality",
                        "url": "https://venue.example/menu",
                        "text": "Duplicate search page",
                    },
                    {
                        "claim_id": "quality",
                        "url": "https://press.example/review",
                        "text": "Unique search page",
                    },
                ],
                "evidence": [],
            }
        },
    }

    class CountingPlaces(FakePlaces):
        def __init__(self, current_fixture: dict) -> None:
            super().__init__(current_fixture)
            self.detail_calls = 0

        def details(self, place_id: str, local_language: str) -> dict:
            self.detail_calls += 1
            return super().details(place_id, local_language)

    class CountingWeb(FakeWeb):
        def __init__(self, current_fixture: dict) -> None:
            super().__init__(current_fixture)
            self.official_calls = 0

        def official_pages(self, candidate: dict, current_details: dict) -> dict:
            self.official_calls += 1
            return super().official_pages(candidate, current_details)

    places = CountingPlaces(fixture)
    web = CountingWeb(fixture)
    candidate = Candidate.from_place(operational_place())
    engine = Funnel(load_config(), RecordedAdapters(places, web, FakeModel(parsed)), now=NOW)
    structured = StructuredRequest.from_dict(parsed)

    mined = engine.stage3_mine([candidate], structured, {}, "full")

    assert web.official_calls == 1
    assert [page["text"] for page in mined["p1"]["pages"]] == [
        "Official home page",
        "Official menu page",
        "Unique search page",
    ]
    assert candidate.detail_payload == details

    engine.stage4_verify([candidate], structured, {}, mined)

    assert places.detail_calls == 1
    assert candidate.detail_payload is None


def test_official_product_type_page_supports_custom_and_card_claims() -> None:
    candidate = Candidate.from_place(operational_place(primary_type="bar"))
    candidate.details = {"websiteUri": "https://venue.example/"}
    page = {
        "claim_id": "product_inventory",
        "url": "https://venue.example/menu",
        "text": "Our craft cocktail menu includes a house martini.",
        "quote": "craft cocktail menu",
        "retrieved_at": "2026-08-26T10:00:00+00:00",
        "source_kind": "official",
        "identity_label": "exact-venue",
    }
    card = {"load_bearing_claims": ["product_inventory"]}
    engine = Funnel(load_config(), RecordedAdapters(None, None, None), now=NOW)

    custom_ledger = engine._build_ledger(
        candidate,
        StructuredRequest.from_dict(
            request(
                [
                    {
                        "claim_id": "craft_cocktail_menu",
                        "claim_type": "product_inventory",
                        "text": "A craft cocktail menu is available",
                    }
                ]
            )
        ),
        card,
        {"pages": [page]},
        operational_details()["en"],
        None,
        None,
    )
    custom_evidence = next(row for row in custom_ledger.evidence if row.claim_id == "craft_cocktail_menu")
    custom_ledger.compute(
        [
            {
                "claim_id": "craft_cocktail_menu",
                "evidence_id": custom_evidence.evidence_id,
                "quote": "craft cocktail menu",
                "entails": True,
            }
        ],
        load_config()["freshness_days"],
        now=NOW,
    )

    card_ledger = engine._build_ledger(
        candidate,
        StructuredRequest.from_dict(request()),
        card,
        {"pages": [page]},
        operational_details()["en"],
        None,
        None,
    )
    card_evidence = next(row for row in card_ledger.evidence if row.claim_id == "product_inventory")
    card_ledger.compute(
        [
            {
                "claim_id": "product_inventory",
                "evidence_id": card_evidence.evidence_id,
                "quote": "craft cocktail menu",
                "entails": True,
            }
        ],
        load_config()["freshness_days"],
        now=NOW,
    )

    assert next(claim for claim in custom_ledger.claims if claim.claim_id == "craft_cocktail_menu").status.value == "supported"
    assert next(claim for claim in card_ledger.claims if claim.claim_id == "product_inventory").status.value == "supported"


def test_recorded_full_run_replays_without_fetching_network(monkeypatch, tmp_path) -> None:
    parsed = request()
    details = operational_details()
    details["en"]["websiteUri"] = "https://venue.example/"
    fixture = {
        "sweep": [operational_place()],
        "details": {"p1": details},
        "official_pages": {"p1": {"pages": [], "evidence": []}},
        "mined": {"p1": {"pages": [], "evidence": []}},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path / "drafts"
    raw_input = {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    recorder = SnapshotRecorder(tmp_path / "run")
    recorded = Funnel(
        config,
        RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), LiteralPageModel(parsed), recorder=recorder),
        now=recorder.run_at,
    ).run(raw_input)
    recorder.finish(raw_input, recorded)
    snapshot = json.loads((tmp_path / "run" / "run.json").read_text())
    assert any(call["adapter"] == "web" and call["operation"] == "official_pages" for call in snapshot["calls"])

    monkeypatch.setattr(
        "osusume.adapters.urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("replay touched the network")),
    )
    replayed = Funnel(
        config,
        RecordedAdapters(None, None, None, replay=ReplayStore(tmp_path / "run")),
        now=recorder.run_at,
    ).run(raw_input)

    assert replayed == recorded


class FailingDirectionsPlaces(FakePlaces):
    def __init__(self, fixture: dict, failed_routes: set[tuple[str, str]]) -> None:
        super().__init__(fixture)
        self.failed_routes = failed_routes
        self.direction_calls = []

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
        self.direction_calls.append((start, end, start_is_place_id, end_is_place_id, mode))
        if (start, end) in self.failed_routes:
            raise AdapterError("no directions returned")
        return super().directions(
            start,
            end,
            departure_time,
            start_is_place_id=start_is_place_id,
            end_is_place_id=end_is_place_id,
            mode=mode,
        )


def test_arrival_window_must_fit_opening_hours() -> None:
    details = operational_details()["en"]
    assert open_for_window(details, "2026-08-26T12:00:00+02:00", "2026-08-26T13:00:00+02:00") == HoursWindowStatus.OPEN
    assert open_for_window(details, "2026-08-26T21:00:00+02:00", "2026-08-26T22:00:00+02:00") == HoursWindowStatus.CLOSED


def test_hours_supplement_uses_places_api_new_periods_and_utc_offset() -> None:
    details = {
        "hours_supplement": {
            "regularOpeningHours": {
                "periods": [
                    {
                        "open": {"day": 3, "hour": 9, "minute": 30},
                        "close": {"day": 3, "hour": 20, "minute": 0},
                    }
                ]
            },
            "utcOffsetMinutes": 120,
        }
    }
    assert open_for_window(details, "2026-08-26T10:00:00Z", "2026-08-26T11:00:00Z") == HoursWindowStatus.OPEN


def test_open_supplement_hours_support_ledger_claim(tmp_path) -> None:
    parsed = request()
    parsed["arrival_start"] = "2026-08-28T10:00:00+02:00"
    parsed["arrival_end"] = "2026-08-28T11:00:00+02:00"
    details = operational_details()
    details["en"].pop("regularOpeningHours")
    details["local"].pop("regularOpeningHours")
    details["hours_supplement"] = {
        "regularOpeningHours": {
            "periods": [
                {
                    "open": {"day": 5, "hour": 9, "minute": 30},
                    "close": {"day": 5, "hour": 12, "minute": 30},
                }
            ]
        },
        "utcOffsetMinutes": 120,
    }
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": details},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )

    hours = next(row for row in output["candidates"][0]["claims"] if row["claim_id"] == "hours_at_arrival")
    assert hours["status"] == "supported"
    assert hours["qualified_evidence_ids"] == ["places_hours"]


def test_missing_hours_are_unknown() -> None:
    assert open_for_window({}, "2026-08-26T12:00:00+02:00", "2026-08-26T13:00:00+02:00") == HoursWindowStatus.UNKNOWN


def test_true_detour_uses_route_legs_not_corridor_distance() -> None:
    assert true_detour_minutes({"duration": "3600s"}, {"duration": "2400s"}, {"duration": "2100s"}) == 15


def test_near_scope_rejects_out_of_radius_but_route_scope_is_exempt() -> None:
    far = operational_place("far", "Far Away")
    far["location"] = {"latitude": 42.5, "longitude": 12.0}
    fixture = {"sweep": [far]}

    near_engine = Funnel({}, RecordedAdapters(FakePlaces(fixture), None, None), now=NOW)
    assert near_engine.stage1_sweep(StructuredRequest.from_dict(request()), {}) == []
    assert near_engine.rejected[0].rejection_reason == "out_of_scope"

    route_engine = Funnel({}, RecordedAdapters(FakePlaces(fixture), None, None), now=NOW)
    survivors = route_engine.stage1_sweep(StructuredRequest.from_dict(request(route=True)), {})
    assert [candidate.place_id for candidate in survivors] == ["far"]


def test_dead_injected_candidate_dies_at_deep_verify(tmp_path) -> None:
    parsed = request()
    fixture = {
        "sweep": [],
        "registry": {"qualifications": [], "injected": [{"name": "Blog Ceramics"}]},
        "resolved": {"Blog Ceramics": operational_place("dead", "Blog Ceramics")},
        "details": {"dead": operational_details("dead", "CLOSED_PERMANENTLY")},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    engine = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW)
    output = engine.run({"ask": parsed["ask"], "card": "salumeria", "depth": "full"})
    assert output["candidates"][0]["verdict"] == "rejected"
    assert output["candidates"][0]["reason"] == "dead_or_status_conflict_at_deep_verify"


def test_exclusion_never_surfaces(tmp_path) -> None:
    parsed = request()
    parsed["exclusions"] = ["Moretti"]
    fixture = {"sweep": [operational_place("moretti", "Ceramiche Moretti")], "registry": {"qualifications": [], "injected": []}}
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )
    assert output["candidates"] == []
    assert output["exclusions_applied"] == ["Moretti"]
    assert "Moretti" not in " ".join(output["widen_options"])


def test_detour_budget_rejects_candidate(tmp_path) -> None:
    parsed = request(route=True)
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
        "directions": {
            "A|B": {"duration": "3600s"},
            "A|p1": {"duration": "2400s"},
            "p1|B": {"duration": "2400s"},
        },
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )
    assert output["candidates"][0]["reason"] == "detour_over_budget"
    assert output["widen_options"] == [
        "increase the detour budget",
        "allow cousin categories",
        "ask venues directly",
    ]
    assert output["widen_candidates"] == [
        {"name": "Test Place", "place_id": "p1", "minutes": 20.0, "mode": "drive"}
    ]
    assert output["human"].splitlines()[-1] == "Just outside the budget: Test Place (20 min drive)"


def test_failed_candidate_detour_stays_unknown_and_run_continues(tmp_path) -> None:
    parsed = request(route=True)
    fixture = {
        "sweep": [operational_place("p1", "Broken Route"), operational_place("p2", "Working Route")],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}, "p2": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details("p1"), "p2": operational_details("p2")},
        "directions": {
            "A|B": {"duration": "3600s"},
            "A|p2": {"duration": "2100s"},
            "p2|B": {"duration": "2100s"},
        },
    }
    places = FailingDirectionsPlaces(fixture, {("A", "p1")})
    recorder = SnapshotRecorder(tmp_path / "run")
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    raw_input = {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    output = Funnel(
        config,
        RecordedAdapters(places, FakeWeb(fixture), FakeModel(parsed), recorder=recorder),
        now=NOW,
    ).run(raw_input)
    recorder.finish(raw_input, output)

    by_id = {candidate["place_id"]: candidate for candidate in output["candidates"]}
    failed_detour = next(claim for claim in by_id["p1"]["claims"] if claim["claim_id"] == "detour")
    assert failed_detour["status"] == "unknown"
    assert by_id["p1"]["verdict"] == "unconfirmed"
    assert by_id["p2"]["verdict"] == "cleared"
    assert places.direction_calls == [
        ("A", "B", False, False, "drive"),
        ("A", "p1", False, True, "drive"),
        ("A", "p2", False, True, "drive"),
        ("p2", "B", True, False, "drive"),
    ]
    snapshot = json.loads((tmp_path / "run" / "run.json").read_text())
    failed_calls = [call for call in snapshot["calls"] if call.get("error")]
    assert failed_calls == [
        {
            "adapter": "goplaces",
            "operation": "directions",
            "request": {
                "start": "A",
                "end": "p1",
                "departure_time": parsed["arrival_start"],
                "start_is_place_id": False,
                "end_is_place_id": True,
            },
            "error": "no directions returned",
        }
    ]


def test_failed_direct_route_leaves_every_detour_unknown(tmp_path) -> None:
    parsed = request(route=True)
    fixture = {
        "sweep": [operational_place("p1", "First"), operational_place("p2", "Second")],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}, "p2": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details("p1"), "p2": operational_details("p2")},
    }
    places = FailingDirectionsPlaces(fixture, {("A", "B")})
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(places, FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )

    assert places.direction_calls == [("A", "B", False, False, "drive")]
    for candidate in output["candidates"]:
        detour = next(claim for claim in candidate["claims"] if claim["claim_id"] == "detour")
        assert detour["status"] == "unknown"
        assert candidate["verdict"] == "unconfirmed"


def test_closed_at_arrival_window_rejects_candidate(tmp_path) -> None:
    parsed = request()
    parsed["arrival_start"] = "2026-08-26T21:00:00+02:00"
    parsed["arrival_end"] = "2026-08-26T22:00:00+02:00"
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )
    assert output["candidates"][0]["reason"] == "closed_at_arrival_window"


def test_unknown_hours_are_unconfirmed_with_contact_draft(tmp_path) -> None:
    parsed = request()
    details = operational_details()
    details["en"].pop("regularOpeningHours")
    details["local"].pop("regularOpeningHours")
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": details},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full", "contact_drafts": True}
    )
    candidate = output["candidates"][0]
    hours = next(row for row in candidate["claims"] if row["claim_id"] == "hours_at_arrival")
    assert candidate["verdict"] == "unconfirmed"
    assert candidate["reason"] is None
    assert hours["status"] == "unknown"
    assert candidate["proposed_contact"]["settles_claim"] == "hours_at_arrival"


def test_spanish_hours_contact_uses_local_language_and_english_translation(tmp_path) -> None:
    parsed = request()
    parsed["local_language"] = "es"
    details = operational_details()
    details["en"].pop("regularOpeningHours")
    details["local"].pop("regularOpeningHours")
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": details},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path

    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full", "contact_drafts": True}
    )

    assert output["candidates"][0]["proposed_contact"] == {
        "channel": "WhatsApp or phone",
        "message": "¿Estarán abiertos durante nuestro horario de llegada?",
        "translation": "Will you be open during our arrival window?",
        "settles_claim": "hours_at_arrival",
    }


def test_unknown_language_hours_contact_falls_back_to_english(tmp_path) -> None:
    parsed = request()
    parsed["local_language"] = "xx"
    details = operational_details()
    details["en"].pop("regularOpeningHours")
    details["local"].pop("regularOpeningHours")
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": details},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path

    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full", "contact_drafts": True}
    )

    contact = output["candidates"][0]["proposed_contact"]
    assert contact["message"] == "Will you be open during our arrival window?"
    assert contact["translation"] == "Will you be open during our arrival window?"


def test_card_without_contact_question_proposes_no_layout_draft(tmp_path) -> None:
    required = {"claim_id": "layout", "claim_type": "layout", "text": "Outdoor tables are available"}
    parsed = request([required])
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path

    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "cocktail_bar", "depth": "full", "contact_drafts": True}
    )

    candidate = output["candidates"][0]
    assert candidate["verdict"] == "unconfirmed"
    assert candidate["proposed_contact"] is None


def test_required_attributes_do_not_duplicate_card_claim_rows(tmp_path) -> None:
    parsed = request(
        [
            {"claim_id": "requested_product", "claim_type": "product_inventory", "text": "Handmade ceramics are sold"},
            {"claim_id": "requested_quality", "claim_type": "quality", "text": "The ceramics are artisanal"},
        ]
    )
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )

    claims = output["candidates"][0]["claims"]
    assert len({claim["claim_id"] for claim in claims}) == len(claims)
    assert len({(claim["claim_type"], claim["text"]) for claim in claims}) == len(claims)
    assert [claim["claim_id"] for claim in claims if claim["claim_type"] == "product_inventory"] == ["requested_product"]
    assert [claim["claim_id"] for claim in claims if claim["claim_type"] == "quality"] == ["requested_quality"]


def test_near_scope_has_no_detour_claim_even_when_card_declares_it(tmp_path) -> None:
    parsed = request()
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    output = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full"}
    )

    assert "detour" not in {claim["claim_type"] for claim in output["candidates"][0]["claims"]}
