"""Exercise the shipped London and Paris seeds through Stage 2, offline."""
from pathlib import Path

import pytest

from osusume.adapters import RecordedAdapters, WebAdapter
from osusume.cards import load_card
from osusume.config import load_config
from osusume.domain import StructuredRequest
from osusume.funnel import Funnel
from osusume.guide_registry import entry_query, load_guide_registry
from tests.helpers import FakePlaces, operational_place

ROOT = Path(__file__).resolve().parents[1]
LONDON = {"Restaurant Gordon Ramsay": 3, "CORE by Clare Smyth": 3, "The Ledbury": 3}
PARIS = {"Arpège": 3, "Le Cinq": 3, "Plénitude - Cheval Blanc Paris": 3}


def request(country: str, city: str, place: str, lat: float, lng: float, language: str) -> StructuredRequest:
    return StructuredRequest.from_dict(
        {
            "ask": f"fine dining in {city}",
            "category": "restaurant",
            "country": country,
            "local_language": language,
            "scope": {"kind": "anchor", "place": place, "mode": "drive", "max_min": 20, "lat": lat, "lng": lng, "city": city},
        }
    )


def seeded_stage2(monkeypatch, country: str, targets: dict[str, int], scoped: StructuredRequest):
    rows = load_guide_registry(country)
    assert rows is not None
    starred = {row["name"]: row for row in rows if row["guide"] == "michelin" and row["name"] in targets}
    assert set(starred) == set(targets)
    assert {name: row["level"] for name, row in starred.items()} == targets

    resolved = {}
    for name, row in starred.items():
        place = operational_place(f"seed-{name}", name, "restaurant")
        place.update(
            location={"latitude": row["latitude"], "longitude": row["longitude"]},
            formatted_address=f"{row['address']}, {row['locality']}",
        )
        resolved[entry_query(row)] = place
    card = load_card(ROOT / f"cards/restaurant_{country.casefold()}.yaml", load_config()["freshness_days"])
    web = WebAdapter("https://unused.test")
    monkeypatch.setattr(
        web,
        "_search",
        lambda query: pytest.fail(f"seeded guides must not search Exa: {query}"),
    )
    engine = Funnel({}, RecordedAdapters(FakePlaces({"resolved": resolved}), web, None))
    return engine.stage2_qualify([], scoped, card)


def test_shipped_seed_injects_london_three_star_restaurants(monkeypatch) -> None:
    scoped = request("GB", "London", "Piccadilly Circus, London", 51.5098, -0.1342, "en")

    candidates = seeded_stage2(monkeypatch, "GB", LONDON, scoped)

    assert set(LONDON) <= {candidate.name for candidate in candidates}
    for candidate in candidates:
        assert candidate.source_weight >= 1.0
        assert any(row["source"] in {"michelin", "fifty_best"} for row in candidate.registry)


def test_shipped_seed_injects_paris_three_star_restaurants(monkeypatch) -> None:
    scoped = request("FR", "Paris", "Place de la Concorde, Paris", 48.8656, 2.3212, "fr")

    candidates = seeded_stage2(monkeypatch, "FR", PARIS, scoped)

    assert set(PARIS) <= {candidate.name for candidate in candidates}
    for candidate in candidates:
        assert candidate.source_weight >= 1.0
        assert any(row["source"] in {"michelin", "fifty_best"} for row in candidate.registry)


def test_seeded_countries_only_name_guides_the_registry_holds() -> None:
    freshness = load_config()["freshness_days"]
    for country in ("ES", "GB", "FR"):
        card = load_card(ROOT / f"cards/restaurant_{country.casefold()}.yaml", freshness)
        rows = load_guide_registry(country)
        assert rows, f"{country} registry is missing"
        assert set(card["sources"][country]) == {row["guide"] for row in rows}
