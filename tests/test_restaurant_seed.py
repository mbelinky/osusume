"""Exercise the shipped seed through Stage 2 without model or network calls."""
from pathlib import Path

from osusume.adapters import RecordedAdapters, WebAdapter
from osusume.cards import load_card
from osusume.config import load_config
from osusume.domain import Candidate
from osusume.funnel import Funnel
from osusume.guide_registry import entry_query, load_guide_registry
from tests.helpers import FakePlaces, operational_place
from tests.test_guide_registry import request

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"Cocina Hermanos Torres", "Disfrutar", "Cinc Sentits", "Enigma", "Suto"}


def test_shipped_seed_injects_missed_barcelona_stars_and_qualifies_survivor(monkeypatch):
    rows = load_guide_registry("ES")
    card = load_card(ROOT / 'cards/restaurant_es.yaml', load_config()['freshness_days'])
    starred = {row['name']: row for row in rows if row['guide'] == 'michelin' and row['name'] in TARGETS}
    assert set(starred) == TARGETS
    assert starred['Cocina Hermanos Torres']['level'] == 3
    assert starred['Suto']['level'] == 1
    resolved = {}
    places_by_name = {}
    for name, row in starred.items():
        place = operational_place(f"seed-{name}", name, "restaurant")
        place.update(location={'latitude': row['latitude'], 'longitude': row['longitude']},
                     formatted_address=f"{row['address']}, {row['locality']}, Spain")
        resolved[entry_query(row)] = place
        places_by_name[name] = place
    places = FakePlaces({'resolved': resolved})
    web = WebAdapter('https://unused.test')
    monkeypatch.setattr(web, '_search', lambda query: (_ for _ in ()).throw(AssertionError('local guides must not search Exa')))
    engine = Funnel({}, RecordedAdapters(places, web, None))
    survivor = Candidate.from_place(places_by_name['Disfrutar'])
    scoped_request = request(kind='anchor', place='Plaça de Catalunya, Barcelona', mode='drive', max_min=20)
    result = engine.stage2_qualify([survivor], scoped_request, card)
    assert {candidate.name for candidate in result} == TARGETS
    for candidate in result:
        assert candidate.source_weight >= 1.0
        assert any(row['source'] == 'michelin' and row['entry_type'] == 'rated_entry' for row in candidate.registry)
        assert all(row['date'] == row['verified_at'] for row in candidate.registry)


def test_seed_keeps_repsol_recommended_out_of_soles():
    rows = load_guide_registry('ES')
    repsol = {row['name'].casefold(): row for row in rows if row['guide'] == 'repsol'}
    assert repsol['suto']['level'] == 1
    assert repsol['sato i tanaka']['level'] == 1
    assert 'sensato' not in repsol
    assert 'os-kuro' not in repsol
