import json
from copy import deepcopy
from datetime import datetime, timezone

from osusume.adapters import AdapterError, RecordedAdapters, SnapshotRecorder
from osusume.config import load_config
from osusume.domain import StructuredRequest
from osusume.funnel import Funnel, HoursWindowStatus, open_for_window, true_detour_minutes
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request


NOW = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)


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
