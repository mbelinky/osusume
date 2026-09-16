from pathlib import Path

import pytest
import yaml

from scripts import refresh_guide_registry as refresh


def configure_refresh(monkeypatch, tmp_path, *, dry_run=False, failing=False):
    output = tmp_path / 'registry.yaml'
    before = 'country: ES\nentries: []\n'
    output.write_text(before)
    monkeypatch.setattr(refresh, 'ROOT', tmp_path)
    monkeypatch.setattr(refresh.sys, 'argv', ['refresh', '--output', str(output), *(['--dry-run'] if dry_run else [])])

    class Fetch:
        def __init__(self, *args):
            self.dates = ['2026-09-10']

    monkeypatch.setattr(refresh, 'Fetcher', Fetch)
    monkeypatch.setattr(refresh, 'crawl_michelin', lambda fetch, stamp, country='ES': [{'guide':'michelin', 'province':'Barcelona', 'locality':'Barcelona', 'name':'Suto', 'verified_at':stamp}])

    def repsol(fetch, stamp):
        if failing:
            raise ValueError('Incomplete listing')
        return []

    monkeypatch.setattr('osusume.repsol_registry.crawl_repsol', repsol)
    monkeypatch.setattr('osusume.fifty_best_registry.crawl_fifty_best',
                        lambda fetch, stamp, country='ES', unresolved=None: [])
    monkeypatch.setattr('osusume.macarfi_registry.crawl_macarfi', lambda fetch, stamp: [])
    monkeypatch.setattr('osusume.hardens_registry.crawl_hardens', lambda fetch, stamp: [])
    monkeypatch.setattr('osusume.le_fooding_registry.crawl_le_fooding', lambda fetch, stamp: [])
    return output, before


def test_dry_run_prints_diff_without_rewriting(monkeypatch, tmp_path, capsys):
    output, before = configure_refresh(monkeypatch, tmp_path, dry_run=True)
    refresh.main()
    assert output.read_text() == before
    assert '+  name: Suto' in capsys.readouterr().out


def test_failed_crawl_preserves_existing_registry(monkeypatch, tmp_path):
    output, before = configure_refresh(monkeypatch, tmp_path, failing=True)
    with pytest.raises(ValueError, match='Incomplete listing'):
        refresh.main()
    assert output.read_text() == before


def test_cached_refresh_does_not_advance_verified_at(monkeypatch, tmp_path):
    output, _ = configure_refresh(monkeypatch, tmp_path)
    refresh.main()
    assert yaml.safe_load(output.read_text())['entries'][0]['verified_at'] == '2026-09-10'


def test_notes_report_missing_added_level_and_name_changes():
    notes = {'michelin': {1: {'Barcelona': ['Old name', 'Missing']}},
             'name_correspondences': {'Old name': 'Official name'}}
    rows = [
        {'guide':'michelin', 'province':'Barcelona', 'locality':'Barcelona', 'name':'Official name', 'level':2, 'url':'https://guide.michelin.com/official'},
        {'guide':'michelin', 'province':'Barcelona', 'locality':'Barcelona', 'name':'Additional', 'level':1, 'url':'https://guide.michelin.com/additional'},
    ]
    differences = refresh.compare_notes(rows, notes)
    assert any('Missing from' in line and 'Missing (' in line for line in differences)
    assert any('Additional official entry: Additional' in line for line in differences)
    assert any('Level differs: Old name' in line for line in differences)
    assert any('Name differs: Old name → Official name' in line for line in differences)


def test_location_enrichment_requires_same_name_and_town():
    rows = [
        {'guide':'michelin', 'name':'Suto', 'locality':'Barcelona', 'latitude':41.4, 'longitude':2.1,
         'url':'https://guide.michelin.com/suto', 'verified_at':'2026-09-10'},
        {'guide':'repsol', 'name':'Suto', 'locality':'Barcelona', 'verified_at':'2026-09-16'},
        {'guide':'repsol', 'name':'Suto', 'locality':'Madrid', 'verified_at':'2026-09-16'},
        {'guide':'repsol', 'name':'Suto catering', 'locality':'Barcelona', 'verified_at':'2026-09-16'},
    ]
    refresh.enrich_locations(rows)
    assert rows[1]['latitude'] == 41.4
    assert rows[1]['verified_at'] == '2026-09-10'
    assert 'latitude' not in rows[2]
    assert 'latitude' not in rows[3]


def test_every_country_binds_a_crawler_to_each_seeded_guide():
    for country in refresh.COUNTRY_GUIDES:
        bound = refresh.guide_crawlers(country, [])
        assert [guide for guide, _ in bound] == list(refresh.COUNTRY_GUIDES[country])
        assert all(callable(crawl) for _, crawl in bound)


def test_fetcher_sends_requested_headers_and_keys_the_cache_by_them(monkeypatch, tmp_path):
    calls = []

    class Completed:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ''

    def run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(refresh.subprocess, 'run', run)
    fetch = refresh.Fetcher(tmp_path, delay=0)
    plain = fetch('https://example.test/listing')
    with_headers = fetch('https://example.test/listing', headers=('Accept: application/json',))

    assert plain == with_headers == '{"ok": true}'
    assert '--header' not in calls[0]
    assert calls[1][calls[1].index('--header') + 1] == 'Accept: application/json'
    # Two cache entries: the same URL answers differently once headers change.
    assert len(list(tmp_path.glob('*.html.gz'))) == 2


def test_rebuild_keeps_previously_resolved_locations(tmp_path):
    path = tmp_path / 'previous.yaml'
    path.write_text(yaml.safe_dump({'format_version': 1, 'country': 'ES', 'entries': [
        {'guide': 'repsol', 'url': 'https://guiarepsol.com/a', 'name': 'A', 'latitude': 41.4,
         'longitude': 2.1, 'place_id': 'abc', 'location_source': 'places'},
        {'guide': 'michelin', 'url': 'https://guide.michelin.com/b', 'name': 'B',
         'latitude': 1.0, 'longitude': 2.0},
    ]}))
    entries = [
        {'guide': 'repsol', 'url': 'https://guiarepsol.com/a', 'name': 'A'},
        {'guide': 'michelin', 'url': 'https://guide.michelin.com/b', 'name': 'B',
         'latitude': 48.8, 'longitude': 2.3},
        {'guide': 'macarfi', 'url': 'https://macarfi.com/c', 'name': 'C'},
    ]

    assert refresh.merge_previous_locations(entries, path) == 1
    assert entries[0]['latitude'] == 41.4 and entries[0]['place_id'] == 'abc'
    # A fresh crawl that carries its own position keeps it.
    assert entries[1]['latitude'] == 48.8
    assert 'latitude' not in entries[2]


def test_rebuild_reports_rows_the_crawl_no_longer_lists(tmp_path, capsys):
    path = tmp_path / 'previous.yaml'
    path.write_text(yaml.safe_dump({'format_version': 1, 'country': 'ES', 'entries': [
        {'guide': 'repsol', 'url': 'https://guiarepsol.com/a', 'name': 'A', 'locality': 'Barcelona'},
        {'guide': 'repsol', 'url': 'https://guiarepsol.com/gone', 'name': 'Gone', 'locality': 'Madrid'},
    ]}))

    refresh.report_preserved([{'guide': 'repsol', 'url': 'https://guiarepsol.com/a'}], path)

    report = capsys.readouterr().err
    assert 'registry rows: 2 before, 1 after' in report
    assert 'dropped: repsol Gone (Madrid)' in report
