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
# Bib Gourmand is its own guide, so every one of these is level 1.
LONDON_BIB = {"Berenjak": 1, "Brutto": 1, "Bancone": 1}
PARIS_BIB = {"Abri Soba": 1, "Adami": 1, "Aux Plumes": 1}


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


def seeded_stage2(monkeypatch, country: str, targets: dict[str, int], scoped: StructuredRequest,
                  guide: str = "michelin"):
    rows = load_guide_registry(country)
    assert rows is not None
    starred = {row["name"]: row for row in rows if row["guide"] == guide and row["name"] in targets}
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


def test_shipped_seed_injects_london_bib_gourmand_restaurants(monkeypatch) -> None:
    scoped = request("GB", "London", "Piccadilly Circus, London", 51.5098, -0.1342, "en")

    candidates = seeded_stage2(monkeypatch, "GB", LONDON_BIB, scoped, guide="michelin_bib")

    assert set(LONDON_BIB) <= {candidate.name for candidate in candidates}
    for name in LONDON_BIB:
        entry = next(candidate for candidate in candidates if candidate.name == name)
        bib = [row for row in entry.registry if row["source"] == "michelin_bib"]
        assert bib and {row["level"] for row in bib} == {1}
        assert "Bib Gourmand" in bib[0]["text"]


def test_shipped_seed_injects_paris_bib_gourmand_restaurants(monkeypatch) -> None:
    scoped = request("FR", "Paris", "Place de la Concorde, Paris", 48.8656, 2.3212, "fr")

    candidates = seeded_stage2(monkeypatch, "FR", PARIS_BIB, scoped, guide="michelin_bib")

    assert set(PARIS_BIB) <= {candidate.name for candidate in candidates}
    for name in PARIS_BIB:
        entry = next(candidate for candidate in candidates if candidate.name == name)
        assert any(row["source"] == "michelin_bib" and row["level"] == 1 for row in entry.registry)


def test_bib_rows_are_level_one_and_never_outrank_a_star() -> None:
    freshness = load_config()["freshness_days"]
    for country, city in (("GB", "London"), ("FR", "Paris")):
        rows = load_guide_registry(country) or []
        bib = [row for row in rows if row["guide"] == "michelin_bib"]
        assert bib, f"{country} holds no Bib Gourmand rows"
        assert {row["level"] for row in bib} == {1}
        assert all(row["url"].startswith("https://guide.michelin.com/") for row in bib)
        assert any(row["locality"] == city for row in bib)
        weights = load_card(ROOT / f"cards/restaurant_{country.casefold()}.yaml", freshness)["sources"][country]
        # Stage 2 orders injections by card weight first, so a Bib must sit under
        # every guide whose rows can carry a higher award.
        for stronger in ("michelin", "fifty_best", "hardens"):
            if stronger in weights:
                assert weights["michelin_bib"] < weights[stronger]


def test_seeded_countries_only_name_guides_the_registry_holds() -> None:
    freshness = load_config()["freshness_days"]
    for country in ("ES", "GB", "FR"):
        card = load_card(ROOT / f"cards/restaurant_{country.casefold()}.yaml", freshness)
        rows = load_guide_registry(country)
        assert rows, f"{country} registry is missing"
        assert set(card["sources"][country]) == {row["guide"] for row in rows}
