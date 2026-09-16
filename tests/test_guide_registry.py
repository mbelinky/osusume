from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from osusume.adapters import RecordedAdapters, WebAdapter
from osusume.domain import Candidate, StructuredRequest
from osusume.funnel import Funnel
from osusume.guide_registry import (
    GuideRegistryError,
    entry_query,
    load_guide_registry,
    names_match,
    registry_entry_matches_candidate,
)
from tests.helpers import FakePlaces, operational_place


def write_registry(directory: Path, entries: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "es_restaurants.yaml").write_text(
        yaml.safe_dump({"format_version": 1, "country": "ES", "entries": entries}, sort_keys=False),
        encoding="utf-8",
    )


def entry(name: str = "Suto", **overrides) -> dict:
    return {
        "name": name,
        "locality": "Barcelona",
        "province": "Barcelona",
        "guide": "michelin",
        "level": 1,
        "url": f"https://guide.michelin.com/es/es/catalunya/barcelona/restaurante/{name.casefold()}",
        "verified_at": "2026-09-16",
        **overrides,
    }


def card(*, sources: dict | None = None) -> dict:
    return {
        "category": "restaurant",
        "reviewed": True,
        "places_types": ["restaurant"],
        "sources": {"ES": sources or {"michelin": 1.0}},
    }


def request(**scope_overrides) -> StructuredRequest:
    scope = {
        "kind": "near",
        "lat": 41.387,
        "lng": 2.170,
        "radius_km": 8,
        "city": "Barcelona",
        **scope_overrides,
    }
    return StructuredRequest.from_dict(
        {
            "ask": "fine dining in Barcelona",
            "category": "restaurant",
            "country": "ES",
            "local_language": "es",
            "scope": scope,
        }
    )


def test_registry_loader_validates_and_normalizes_rows(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry(aliases=["Suto Barcelona"], latitude=41.4, longitude=2.1)])

    rows = load_guide_registry("es", tmp_path)

    assert rows is not None
    assert rows[0]["guide"] == "michelin"
    assert rows[0]["latitude"] == 41.4
    assert names_match("Cocina Hermanos Torres", "Hermanos Torres")
    assert not names_match("Disfrutar Catering", "Disfrutar")


def test_registry_loader_rejects_incomplete_rows(tmp_path: Path) -> None:
    bad = entry()
    bad.pop("verified_at")
    write_registry(tmp_path, [bad])

    with pytest.raises(GuideRegistryError, match="verified_at"):
        load_guide_registry("ES", tmp_path)


def test_registry_loader_accepts_official_repsol_subdomain(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [entry(guide="repsol", url="https://www.guiarepsol.com/es/comer/ficha/suto/")],
    )

    assert load_guide_registry("ES", tmp_path)[0]["guide"] == "repsol"


@pytest.mark.parametrize("level", [True, 1.0, 1.9, "1", 0, 4])
def test_registry_loader_requires_integer_guide_level(tmp_path: Path, level) -> None:
    write_registry(tmp_path, [entry(level=level)])

    with pytest.raises(GuideRegistryError, match="level"):
        load_guide_registry("ES", tmp_path)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"url": "https://example.test/suto"}, "guide.michelin.com"),
        ({"verified_at": "2026-9-16"}, "canonical ISO date"),
        ({"latitude": "41.4", "longitude": 2.1}, "latitude must be numeric"),
        ({"latitude": float("nan"), "longitude": 2.1}, "coordinates are out of range"),
        ({"latitude": 91, "longitude": 2.1}, "coordinates are out of range"),
        ({"latitude": 41.4, "longitude": 181}, "coordinates are out of range"),
    ],
)
def test_registry_loader_rejects_untrusted_url_date_and_coordinates(
    tmp_path: Path, overrides: dict, message: str
) -> None:
    write_registry(tmp_path, [entry(**overrides)])

    with pytest.raises(GuideRegistryError, match=message):
        load_guide_registry("ES", tmp_path)


def test_registry_loader_rejects_non_iso_country_before_building_path(tmp_path: Path) -> None:
    with pytest.raises(GuideRegistryError, match="two-letter ISO"):
        load_guide_registry("../ES", tmp_path)


def test_local_registry_qualifies_survivor_and_injects_scoped_missing_entry(tmp_path: Path, monkeypatch) -> None:
    write_registry(
        tmp_path,
        [entry("Suto"), entry("Disfrutar", level=3), entry("Outside", locality="Vic", province="Barcelona")],
    )
    adapter = WebAdapter("https://unused.test", registry_dir=tmp_path)
    monkeypatch.setattr(adapter, "_search", lambda query: pytest.fail(f"unexpected Exa fallback: {query}"))

    output = adapter.registry(
        request().to_dict(),
        card(),
        [
            {
                "place_id": "suto-id",
                "name": "Suto",
                "formattedAddress": "Carrer d'Enric Granados, Barcelona, Spain",
            }
        ],
    )

    assert [(row["place_id"], row["source"], row["date"]) for row in output["qualifications"]] == [
        ("suto-id", "michelin", "2026-09-16")
    ]
    assert [row["name"] for row in output["injected"]] == ["Disfrutar"]
    assert output["injected"][0]["text"].startswith("Disfrutar (Barcelona) holds 3 Michelin stars, verified ")


def test_local_registry_uses_coordinates_to_limit_injection(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            entry("Nearby", locality="Unknown", province="Unknown", latitude=41.39, longitude=2.17),
            entry("Far Away", locality="Barcelona", province="Barcelona", latitude=40.41, longitude=-3.70),
        ],
    )
    adapter = WebAdapter("https://unused.test", registry_dir=tmp_path)

    output = adapter.registry(
        {**request(city="").to_dict(), "ask": "fine dining"},
        card(),
        [],
    )

    assert [row["name"] for row in output["injected"]] == ["Nearby"]


def test_exa_is_only_fallback_for_source_absent_from_local_registry(tmp_path: Path, monkeypatch) -> None:
    write_registry(tmp_path, [entry()])
    adapter = WebAdapter("https://unused.test", api_key="test", registry_dir=tmp_path)
    queries = []
    monkeypatch.setattr(adapter, "_search", lambda query: queries.append(query) or {"results": []})

    adapter.registry(request().to_dict(), card(sources={"michelin": 1.0, "repsol": 1.0}), [])

    assert len(queries) == 1
    assert queries[0].startswith("repsol ")


def test_stage2_injects_resolved_seed_with_weight_and_provenance(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry()])
    resolved = operational_place("suto-id", "Suto", "restaurant")
    resolved.update(
        {
            "formattedAddress": "Carrer d'Enric Granados, Barcelona, Spain",
            "location": {"latitude": 41.387, "longitude": 2.170},
        }
    )
    places = FakePlaces({"resolved": {"Suto Barcelona": resolved}})
    web = WebAdapter("https://unused.test", registry_dir=tmp_path)
    engine = Funnel({}, RecordedAdapters(places, web, None))

    candidates = engine.stage2_qualify([], request(), card())

    assert [(candidate.name, candidate.source_weight) for candidate in candidates] == [("Suto", 1.0)]
    assert candidates[0].registry[0]["source"] == "michelin"
    assert candidates[0].registry[0]["verified_at"] == "2026-09-16"


def test_stage2_does_not_double_weight_duplicate_guide_rows(tmp_path: Path) -> None:
    second = entry(url="https://guide.michelin.com/es/es/suto-alt")
    write_registry(tmp_path, [entry(), second])
    raw = operational_place("suto-id", "Suto", "restaurant")
    raw["formattedAddress"] = "Carrer d'Enric Granados, Barcelona, Spain"
    candidate = Candidate.from_place(raw)
    engine = Funnel({}, RecordedAdapters(FakePlaces({}), WebAdapter("https://unused.test", registry_dir=tmp_path), None))

    candidates = engine.stage2_qualify([candidate], request(), card())

    assert candidates[0].source_weight == 1.0
    assert len(candidates[0].registry) == 2


def test_stage2_reuses_resolved_place_and_weights_each_guide_once() -> None:
    resolved = operational_place("suto-id", "Suto", "restaurant")
    resolved["formattedAddress"] = "Carrer d'Enric Granados, Barcelona, Spain"
    resolved["location"] = {"latitude": 41.387, "longitude": 2.170}

    class Places(FakePlaces):
        def __init__(self):
            super().__init__({})
            self.calls = 0

        def resolve(self, name, raw_request, place_type=None):
            self.calls += 1
            return resolved

    class Web:
        def registry(self, raw_request, raw_card, candidates=None):
            rows = []
            for guide in ("michelin", "repsol"):
                rows.append(
                    {
                        **entry(guide=guide, url=f"https://example.test/{guide}"),
                        "source": guide,
                        "entry_type": "rated_entry",
                        "registry": "local",
                    }
                )
            return {"qualifications": [], "injected": rows}

    places = Places()
    engine = Funnel({}, RecordedAdapters(places, Web(), None))

    candidates = engine.stage2_qualify([], request(), card(sources={"michelin": 1.0, "repsol": 1.0}))

    assert places.calls == 1
    assert candidates[0].source_weight == 2.0
    assert {row["source"] for row in candidates[0].registry} == {"michelin", "repsol"}


def test_stage2_rejects_wrong_places_identity_or_locality(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry()])
    wrong = operational_place("wrong-id", "Suto Madrid", "restaurant")
    wrong["formattedAddress"] = "Calle Mayor, Madrid, Spain"
    places = FakePlaces({"resolved": {"Suto Barcelona": wrong}})
    engine = Funnel({}, RecordedAdapters(places, WebAdapter("https://unused.test", registry_dir=tmp_path), None))

    candidates = engine.stage2_qualify([], request(), card())

    assert candidates == []
    assert engine.rejected[0].rejection_reason == "registry_identity_mismatch"


def test_ephemeral_card_has_no_registry_call_or_injection() -> None:
    class NoRegistryWeb:
        def registry(self, request, card, candidates=None):
            raise AssertionError("ephemeral guide lane must stay empty")

    original = Candidate.from_place(operational_place())
    engine = Funnel({}, RecordedAdapters(FakePlaces({}), NoRegistryWeb(), None))

    draft = {"category": "restaurant", "reviewed": False, "sources": {"ES": {"michelin": 1.0}}}
    assert engine.stage2_qualify([original], request(), draft) == [original]


MATCHING_PAIRS = [
    ("Angle", "Angle Barcelona"),
    ("Fishølogy", "Fishology Restaurant"),
    ("Atempo", "Atempo Restaurant"),
    ("Aleia", "Aleia Restaurant at Casa Fuster Hotel"),
    ("COME by Paco Méndez", "COME"),
    ("Hisop", "Hisop | Barcelona"),
    ("MAE Barcelona", "Mae"),
    ("Suto", "SUTO barcelona"),
    ("Besta", "Besta Barcelona"),
    ("Brabo", "Brabo | Restaurante Gràcia"),
    ("Eldelmar", "ELDELMAR - HERMANOS TORRES"),
    ("Els Pescadors", "Els Pescadors de Barcelona - Restaurant d'Alta Cuina Marinera"),
    ("Espacio UMA", "UMA"),
    ("Petit Comité Gaig", "Petit Comitè"),
    ("PUR", "PUR Restaurant"),
    ("Xerta", "Xerta Restaurant"),
]

DIFFERENT_VENUES = [
    ("AÜRT", "TRÜ"),
    ("Suculent", "Fat Cat"),
    ("Jardín del Alma", "Restaurant Jardí de l'Ànima"),
]


def place(name: str, **overrides) -> dict:
    return {
        "place_id": "place-id",
        "name": name,
        "formattedAddress": "Carrer d'Enric Granados 1, Barcelona, Spain",
        **overrides,
    }


@pytest.mark.parametrize(("registry_name", "places_name"), MATCHING_PAIRS)
def test_guide_and_places_spellings_of_one_restaurant_match(registry_name: str, places_name: str) -> None:
    assert registry_entry_matches_candidate(entry(registry_name), place(places_name))


@pytest.mark.parametrize(("registry_name", "places_name"), DIFFERENT_VENUES)
def test_different_restaurants_do_not_match(registry_name: str, places_name: str) -> None:
    assert not registry_entry_matches_candidate(entry(registry_name), place(places_name))


def test_shared_address_matches_on_one_telling_word() -> None:
    rated = entry("Enoteca Paco Pérez", latitude=41.385, longitude=2.196)
    resolved = place("Enoteca", location={"latitude": 41.3851, "longitude": 2.1961}, formattedAddress="")

    assert registry_entry_matches_candidate(rated, resolved)
    assert not registry_entry_matches_candidate(rated, {**resolved, "location": {"latitude": 41.40, "longitude": 2.19}})


def test_entry_query_does_not_repeat_the_locality_already_in_the_name() -> None:
    assert entry_query(entry("MAE Barcelona")) == "MAE Barcelona"
    assert entry_query(entry("Suto")) == "Suto Barcelona"
    assert entry_query(entry("Le Cinq", locality="Paris", province="Ile-de-France")) == "Le Cinq Paris Ile-de-France"


def test_registry_loader_accepts_every_configured_guide(tmp_path: Path) -> None:
    write_registry(
        tmp_path,
        [
            entry(guide="fifty_best", url="https://www.theworlds50best.com/list/1-50"),
            entry(guide="repsol", url="https://www.guiarepsol.com/es/comer/ficha/suto/"),
        ],
    )

    assert [row["guide"] for row in load_guide_registry("ES", tmp_path)] == ["fifty_best", "repsol"]


def test_registry_loader_rejects_a_guide_it_does_not_know(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry(guide="gault_millau", url="https://www.gaultmillau.com/suto")])

    with pytest.raises(GuideRegistryError, match="michelin, repsol, fifty_best"):
        load_guide_registry("ES", tmp_path)


def test_registry_loader_holds_each_guide_to_its_own_domain(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry(guide="fifty_best", url="https://guide.michelin.com/en/suto")])

    with pytest.raises(GuideRegistryError, match="theworlds50best.com"):
        load_guide_registry("ES", tmp_path)


def test_registry_loader_keeps_an_optional_city_and_rejects_an_empty_one(tmp_path: Path) -> None:
    write_registry(tmp_path, [entry(city="London")])
    assert load_guide_registry("ES", tmp_path)[0]["city"] == "London"

    write_registry(tmp_path, [entry(city=" ")])
    with pytest.raises(GuideRegistryError, match="city"):
        load_guide_registry("ES", tmp_path)


def test_registry_loader_reads_each_country_from_its_own_file(tmp_path: Path) -> None:
    rows = [
        entry(
            "Core by Clare Smyth",
            locality="London",
            province="Greater London",
            url="https://guide.michelin.com/en/greater-london/london/restaurant/core-by-clare-smyth",
        )
    ]
    (tmp_path / "gb_restaurants.yaml").write_text(
        yaml.safe_dump({"format_version": 1, "country": "GB", "entries": rows}, sort_keys=False),
        encoding="utf-8",
    )

    assert load_guide_registry("GB", tmp_path)[0]["province"] == "Greater London"
    assert load_guide_registry("FR", tmp_path) is None

    (tmp_path / "fr_restaurants.yaml").write_text(
        yaml.safe_dump({"format_version": 1, "country": "GB", "entries": rows}, sort_keys=False),
        encoding="utf-8",
    )
    with pytest.raises(GuideRegistryError, match="country must be FR"):
        load_guide_registry("FR", tmp_path)
