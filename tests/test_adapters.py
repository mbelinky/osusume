import json
from datetime import datetime, timezone

import pytest

from osusume.adapters import AdapterError, GoplacesAdapter, RecordedAdapters, ReplayStore, SnapshotRecorder, WebAdapter
from osusume.config import load_config
from osusume.domain import StructuredRequest
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
    mined = adapter.mine({"place_id": "p1", "name": "Test Place"}, parsed, card)

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
