from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from osusume.adapters import RecordedAdapters
from osusume.cards import CardValidationError, validate_card
from osusume.config import load_config
from osusume.funnel import Funnel
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request


NOW = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
CASES = yaml.safe_load((Path(__file__).parent / "fixtures" / "payloads" / "incidents.yaml").read_text())


def run_case(tmp_path: Path, parsed: dict, fixture: dict, *, contact_drafts: bool = False) -> dict:
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    adapters = RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed))
    output = Funnel(config, adapters, now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "full", "contact_drafts": contact_drafts}
    )
    for candidate in output["candidates"]:
        assert set(candidate["rendered_claim_ids"]) == {claim["claim_id"] for claim in candidate["claims"]}
        assert len(candidate["rendered_claims"]) == len(candidate["claims"])
        assert "This must never render" not in " ".join(candidate["rendered_claims"])
    bad_draft = {
        "category": "bad",
        "languages": {"en": ["bad"]},
        "places_types": ["store"],
        "query_templates": [],
        "load_bearing_claims": ["operational_status", "hours_at_arrival", "detour"],
        "freshness_overrides": {},
        "reviewed": False,
        "sources": {"IT": {"blog": 1.0}},
    }
    with pytest.raises(CardValidationError):
        validate_card(bad_draft, config["freshness_days"])
    return output


@pytest.mark.parametrize("case_name", ["e_f1", "e_f2", "e_f4", "e_f6", "e_stale"])
def test_incident_claims_fail_closed(case_name: str, tmp_path: Path) -> None:
    case = CASES[case_name]
    parsed = request([case["required"]])
    primary_type = "wine_bar" if case_name == "e_f6" else "food_store"
    fixture = {
        "sweep": [operational_place(primary_type=primary_type)],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": [case["evidence"]]}},
        "details": {"p1": operational_details()},
    }
    output = run_case(tmp_path, parsed, fixture)
    candidate = output["candidates"][0]
    claim = next(row for row in candidate["claims"] if row["claim_id"] == case["required"]["claim_id"])
    assert claim["status"] == case["expected_status"]
    assert candidate["verdict"] == case["expected_verdict"]
    if case_name == "e_f1":
        assert output["refusal"] is True
        assert "Market morning" not in output["human"].splitlines()[0]
    if case_name == "e_f2":
        assert "endorsement" not in output["human"].lower()
        evidence = next(row for row in candidate["claims"] if row["claim_id"] == "quality")
        assert evidence["status"] == "wrong_source"
    if case_name == "e_f6":
        assert not any(row["status"] == "supported" and row["claim_id"] == "counter_service" for row in candidate["claims"])


def test_e_f3_dead_shop_dies_at_sweep(tmp_path: Path) -> None:
    parsed = request()
    dead = operational_place("dead", "Closed Ceramics")
    dead["businessStatus"] = CASES["e_f3"]["sweep_business_status"]
    fixture = {"sweep": [dead], "registry": {"qualifications": [], "injected": []}}
    output = run_case(tmp_path, parsed, fixture)
    assert output["candidates"][0]["reason"] == CASES["e_f3"]["expected_reason"]


def test_e_f5_exclusion_never_renders(tmp_path: Path) -> None:
    parsed = request()
    parsed["exclusions"] = [CASES["e_f5"]["exclude"]]
    fixture = {"sweep": [operational_place("moretti", CASES["e_f5"]["candidate_name"])], "registry": {"qualifications": [], "injected": []}}
    output = run_case(tmp_path, parsed, fixture)
    assert len(output["candidates"]) == CASES["e_f5"]["expected_candidates"]
    assert CASES["e_f5"]["candidate_name"] not in output["human"]


def test_e_good_1_detour_clears_at_budget(tmp_path: Path) -> None:
    case = CASES["e_good_1"]
    parsed = request(route=True)
    fixture = {
        "sweep": [operational_place()],
        "registry": {"qualifications": [], "injected": []},
        "mined": {"p1": {"pages": [], "evidence": []}},
        "details": {"p1": operational_details()},
        "directions": {
            "A|B": {"duration_seconds": case["direct_seconds"]},
            "A|p1": {"duration_seconds": case["first_leg_seconds"]},
            "p1|B": {"duration_seconds": case["second_leg_seconds"]},
        },
    }
    output = run_case(tmp_path, parsed, fixture)
    candidate = output["candidates"][0]
    detour = next(row for row in candidate["claims"] if row["claim_id"] == "detour")
    assert detour["status"] == "supported"
    assert candidate["verdict"] == "cleared"


def test_e_good_2_contact_then_venue_reply_upgrade(tmp_path: Path) -> None:
    case = CASES["e_good_2"]
    required = {"claim_id": "counter_service", "claim_type": "counter_service", "text": "Cut-to-order counter service is available"}
    parsed = request([required])
    base = {
        "sweep": [operational_place(primary_type="food_store")],
        "registry": {"qualifications": [], "injected": []},
        "details": {"p1": operational_details()},
    }
    first = {**base, "mined": {"p1": {"pages": [], "evidence": []}}}
    output = run_case(tmp_path, parsed, first, contact_drafts=True)
    assert output["candidates"][0]["verdict"] == "unconfirmed"
    assert output["candidates"][0]["proposed_contact"]["settles_claim"] == "counter_service"

    reply = {
        "evidence_id": "venue_reply",
        "claim_id": "counter_service",
        "source_kind": "venue_reply",
        "url": "reply://whatsapp",
        "retrieved_at": "2026-08-26T10:00:00+00:00",
        "evidence_date": "2026-08-26T10:00:00+00:00",
        "text": case["venue_reply"],
        "quote": case["venue_reply"],
    }
    follow_up = {**base, "mined": {"p1": {"pages": [], "evidence": [reply]}}}
    output = run_case(tmp_path, parsed, follow_up)
    claim = next(row for row in output["candidates"][0]["claims"] if row["claim_id"] == "counter_service")
    assert claim["status"] == "supported"
    assert output["candidates"][0]["verdict"] == case["expected_verdict"]


def test_e_refusal_has_widen_options_and_no_candidates(tmp_path: Path) -> None:
    parsed = request()
    fixture = {"sweep": [], "registry": {"qualifications": [], "injected": []}}
    output = run_case(tmp_path, parsed, fixture)
    assert output["refusal"] is CASES["e_refusal"]["expected_refusal"]
    assert output["candidates"] == []
    assert len(output["widen_options"]) == 4
