"""Prove the shipped Macarfi, Harden's and Le Fooding rows reach Stage 2 offline."""
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
# One known entry per new guide, with the level its published award maps to.
SEEDED = {
    "ES": ("macarfi", {"Disfrutar": 3, "Lasarte": 3}),
    "GB": ("hardens", {"The Ledbury": 3, "Core by Clare Smyth": 2}),
    "FR": ("le_fooding", {"Septime": 1}),
}
ANCHORS = {
    "ES": ("Barcelona", "Plaça de Catalunya, Barcelona", 41.3870, 2.1700, "es"),
    "GB": ("London", "Piccadilly Circus, London", 51.5098, -0.1342, "en"),
    "FR": ("Paris", "Place de la Concorde, Paris", 48.8656, 2.3212, "fr"),
}


def anchored(country: str) -> StructuredRequest:
    city, place, lat, lng, language = ANCHORS[country]
    return StructuredRequest.from_dict(
        {
            "ask": f"fine dining in {city}",
            "category": "restaurant",
            "country": country,
            "local_language": language,
            "scope": {"kind": "anchor", "place": place, "mode": "drive", "max_min": 20,
                      "lat": lat, "lng": lng, "city": city},
        }
    )


def seeded_rows(country: str) -> dict[str, dict]:
    guide, targets = SEEDED[country]
    rows = load_guide_registry(country)
    assert rows is not None, f"{country} registry is missing"
    found = {row["name"]: row for row in rows if row["guide"] == guide and row["name"] in targets}
    assert set(found) == set(targets), f"{guide} is missing {set(targets) - set(found)}"
    assert {name: row["level"] for name, row in found.items()} == targets
    return found


@pytest.mark.parametrize("country", sorted(SEEDED))
def test_shipped_seed_holds_the_new_guides_entries(country: str) -> None:
    guide, _ = SEEDED[country]
    for row in seeded_rows(country).values():
        assert row["guide"] == guide
        assert row["url"].startswith("https://")
        assert row["verified_at"]


def test_hardens_london_rows_carry_the_greater_london_province() -> None:
    for row in seeded_rows("GB").values():
        assert (row["locality"], row["province"]) == ("London", "Greater London")


def test_le_fooding_rows_are_paris_selections() -> None:
    rows = load_guide_registry("FR") or []
    selected = [row for row in rows if row["guide"] == "le_fooding"]
    assert selected, "Le Fooding rows are missing"
    assert {row["level"] for row in selected} == {1}
    assert {(row["locality"], row["province"]) for row in selected} == {("Paris", "Île-de-France")}
    assert {row["name"] for row in selected} >= {"Septime", "Le Chateaubriand"}


def test_macarfi_rows_keep_their_listed_district_and_province() -> None:
    disfrutar = seeded_rows("ES")["Disfrutar"]
    assert disfrutar["province"] == "Barcelona"
    assert disfrutar["locality"] == "L'Eixample Esquerre"
    assert "latitude" in disfrutar and "longitude" in disfrutar


@pytest.mark.parametrize("country", sorted(SEEDED))
def test_new_guide_entries_are_injected_without_searching_exa(monkeypatch, country: str) -> None:
    guide, _ = SEEDED[country]
    rows = seeded_rows(country)
    resolved = {}
    for name, row in rows.items():
        place = operational_place(f"seed-{name}", name, "restaurant")
        place.update(
            location={"latitude": row["latitude"], "longitude": row["longitude"]},
            formatted_address=f"{row.get('address', name)}, {row['locality']}",
        )
        resolved[entry_query(row)] = place
    card = load_card(ROOT / f"cards/restaurant_{country.casefold()}.yaml", load_config()["freshness_days"])
    assert guide in card["sources"][country]
    web = WebAdapter("https://unused.test")
    monkeypatch.setattr(
        web, "_search", lambda query: pytest.fail(f"seeded guides must not search Exa: {query}")
    )
    engine = Funnel({}, RecordedAdapters(FakePlaces({"resolved": resolved}), web, None))

    candidates = engine.stage2_qualify([], anchored(country), card)

    by_name = {candidate.name: candidate for candidate in candidates}
    assert set(rows) <= set(by_name)
    for name in rows:
        assert any(entry["source"] == guide for entry in by_name[name].registry)
