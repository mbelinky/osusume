"""Ranking, sweep hygiene, card selection, and registry quality evidence."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from osusume.adapters import RecordedAdapters, _region_args
from osusume.cards import find_card
from osusume.config import load_config
from osusume.domain import Candidate
from osusume.funnel import Funnel, _rating_score, _type_mismatch
from tests.helpers import FakeModel, FakePlaces, FakeWeb, operational_details, operational_place, request

NOW = datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def _card(**overrides) -> dict:
    card = {
        "category": "restaurant",
        "country": "IT",
        "reviewed": True,
        "languages": {"en": ["restaurant"]},
        "places_types": ["restaurant", "fine_dining_restaurant"],
        "query_templates": ["{name} {city}"],
        "sources": {"IT": {"michelin": 1.0}},
        "source_domains": {"michelin": ["guide.michelin.com"]},
        "load_bearing_claims": ["operational_status", "hours_at_arrival", "detour", "quality"],
        "event_shaped": False,
    }
    card.update(overrides)
    return card


def test_rating_score_prefers_many_reviews_over_a_perfect_handful() -> None:
    ranking = {"prior_rating": 4.2, "prior_weight": 50}
    caterer = Candidate(place_id="a", name="Caterer", rating=5.0, review_count=12)
    starred = Candidate(place_id="b", name="Starred", rating=4.8, review_count=3800)
    assert _rating_score(starred, ranking) > _rating_score(caterer, ranking)
    assert _rating_score(Candidate(place_id="c", name="No rating"), ranking) == 4.2


def test_candidate_reads_the_places_cli_review_count() -> None:
    place = {"place_id": "x", "name": "X", "rating": 4.7, "user_rating_count": 6491, "types": ["restaurant"]}
    assert Candidate.from_place(place).review_count == 6491


def test_type_mismatch_only_when_types_share_nothing_with_the_card() -> None:
    card = _card()
    cooking_school = Candidate(place_id="a", name="School", types=["cooking_school", "point_of_interest"])
    tapas = Candidate(place_id="b", name="Tapas", types=["spanish_restaurant", "restaurant", "food"])
    untyped = Candidate(place_id="c", name="Untyped")
    wanted = set(card["places_types"])
    assert _type_mismatch(cooking_school, wanted)
    assert not _type_mismatch(tapas, wanted)
    assert not _type_mismatch(untyped, wanted)
    assert not _type_mismatch(cooking_school, set())


def test_region_args_follow_the_request_country() -> None:
    assert _region_args({"country": "es"}) == ["--region", "ES"]
    assert _region_args({"country": "Spain"}) == []
    assert _region_args({}) == []


def _run(fixture: dict, parsed: dict, card_name: str, config: dict) -> dict:
    model = FakeModel(parsed)
    return Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), model), now=NOW).run(
        {"ask": parsed["ask"], "card": card_name, "depth": "quick"}
    )


def test_sweep_dedupes_and_ranks_before_the_cut(tmp_path) -> None:
    parsed = request()
    far = deepcopy(operational_place("far", "Far Away", "restaurant"))
    far["location"] = {"latitude": 45.0, "longitude": 12.0}
    fixture = {
        "sweep": [
            operational_place("p1", "Perfect Twelve", "restaurant"),
            operational_place("p1", "Perfect Twelve", "restaurant"),
            operational_place("p2", "Old Favourite", "restaurant"),
            far,
            far,
        ],
        "details": {"p1": operational_details("p1"), "p2": operational_details("p2")},
    }
    fixture["sweep"][0]["rating"] = 5.0
    fixture["sweep"][0]["userRatingCount"] = 12
    fixture["sweep"][2]["rating"] = 4.7
    fixture["sweep"][2]["userRatingCount"] = 2400
    for row in fixture["sweep"]:
        row["types"] = ["food_store", "food"]
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    config["retrieval"]["max_candidates"] = 1
    packet = _run(fixture, parsed, "salumeria", config)
    names = [row["name"] for row in packet["candidates"] if row["verdict"] != "rejected"]
    assert names == ["Old Favourite"]
    assert packet["swept"] == 2
    assert packet["coverage"]["candidates"] == 1
    assert packet["rejected_counts"] == {"out_of_scope": 1}
    assert [row["name"] for row in packet["candidates"] if row["reason"] == "out_of_scope"] == ["Far Away"]


def test_find_card_prefers_the_request_country_and_is_strict_without_an_explicit_name() -> None:
    cards_dir = ROOT / "cards"
    defaults = load_config()["freshness_days"]
    found = find_card("restaurant", cards_dir, defaults, "ES", strict_country=True)
    assert found and found[1].name == "restaurant_es.yaml"
    assert find_card("restaurant", cards_dir, defaults, "XX", strict_country=True) is None
    explicit = find_card("cocktail_bar", cards_dir, defaults, "IT")
    assert explicit and explicit[1].name == "cocktail_bar_es.yaml"


def test_registry_entry_backs_every_quality_claim_by_type(tmp_path) -> None:
    parsed = request(
        [{"claim_id": "michelin_starred", "claim_type": "quality", "text": "Holds a Michelin star", "required": True}]
    )
    parsed["category"] = "restaurant"
    place = operational_place("p1", "Starred", "restaurant")
    place["types"] = ["restaurant"]
    fixture = {
        "sweep": [place],
        "details": {"p1": operational_details("p1")},
        "registry": {
            "qualifications": [
                {
                    "place_id": "p1",
                    "source": "michelin",
                    "entry_type": "rated_entry",
                    "url": "https://guide.michelin.com/x",
                    "text": "Starred is a level 1 rated entry in michelin.",
                    "date": "2026-08-01",
                    "level": 1,
                }
            ],
            "injected": [],
        },
    }
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    config["paths"]["cards"] = tmp_path / "cards"
    config["paths"]["cards"].mkdir()
    import yaml

    (config["paths"]["cards"] / "restaurant_it.yaml").write_text(yaml.safe_dump(_card()), encoding="utf-8")
    packet = _run(fixture, parsed, "restaurant", config)
    starred = next(row for row in packet["candidates"] if row["name"] == "Starred")
    quality = next(claim for claim in starred["claims"] if claim["claim_id"] == "michelin_starred")
    assert quality["status"] == "supported"
    assert starred["verdict"] == "cleared"


def test_quick_task_overrides_apply_only_to_quick_runs(tmp_path, monkeypatch) -> None:
    from osusume.adapters import ModelAdapter
    from osusume.cli import _parser, _run_find

    config = {
        "models": {
            "default_model": "base",
            "task_overrides": {"judge": "slow-judge"},
            "quick_task_overrides": {"judge": "fast-judge"},
            "commands": {"default": ["python3", "-c", "import json,sys; print(json.dumps(dict(model=sys.argv[1])))", "{model}"]},
        }
    }
    assert ModelAdapter(config).run("judge", {}) == {"model": "slow-judge"}
    assert ModelAdapter(config, quick=True).run("judge", {}) == {"model": "fast-judge"}
    assert ModelAdapter(config, quick=True).run("parse", {}) == {"model": "base"}

    built: list[bool] = []

    class SpyModel(FakeModel):
        def __init__(self, config: dict) -> None:
            super().__init__(request())

        def __setattr__(self, name, value):
            if name == "quick":
                built.append(bool(value))
            object.__setattr__(self, name, value)

    fixture = {"sweep": [], "details": {}}
    monkeypatch.setattr("osusume.cli.GoplacesAdapter", lambda: FakePlaces(fixture))
    monkeypatch.setattr("osusume.cli.WebAdapter", lambda *a, **kw: FakeWeb(fixture))
    monkeypatch.setattr("osusume.cli.ModelAdapter", SpyModel)
    run_config = load_config()
    run_config["paths"]["runs"] = tmp_path
    run_config["paths"]["drafts"] = tmp_path / "drafts"
    for extra, expected in ((["--depth", "quick"], True), ([], False), (["--depth", "quick", "--deep-dive"], False)):
        args = _parser().parse_args(["find", "test", "--near", "42.0,12.0", "--card", "salumeria", "--json", *extra])
        assert _run_find(args, run_config) == 0
    assert built == [True, False, False]


def test_when_overrides_a_parse_lane_arrival_window(tmp_path) -> None:
    parsed = request()
    parsed["arrival_start"] = "2026-10-10T20:30:00+02:00"
    parsed["arrival_end"] = "2026-10-10T23:00:00+02:00"
    fixture = {"sweep": [], "details": {}}
    config = load_config()
    config["paths"]["drafts"] = tmp_path
    packet = Funnel(config, RecordedAdapters(FakePlaces(fixture), FakeWeb(fixture), FakeModel(parsed)), now=NOW).run(
        {"ask": parsed["ask"], "card": "salumeria", "depth": "quick", "when": "2026-10-10T20:30:00+02:00"}
    )
    assert packet["request"]["arrival_start"] == "2026-10-10T20:30:00+02:00"
    assert packet["request"]["arrival_end"] == "2026-10-10T20:30:00+02:00"
