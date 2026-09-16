#!/usr/bin/env python3
"""Refresh one country's official guide entries atomically, or print a dry-run diff."""
from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import json
import sys
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from osusume.michelin_registry import crawl_michelin, crawl_michelin_bib  # noqa: E402

# Guides seeded per country, in the order they are crawled and reported.
COUNTRY_GUIDES = {
    "ES": ("michelin", "repsol", "fifty_best", "macarfi"),
    "GB": ("michelin", "michelin_bib", "fifty_best", "hardens"),
    "FR": ("michelin", "michelin_bib", "fifty_best", "le_fooding"),
}
# Position fields a separate enrichment pass adds; a rebuild must keep them.
ENRICHMENT_FIELDS = ("latitude", "longitude", "place_id", "location_source", "location_source_url")


class Fetcher:
    """Serial polite public fetches; raw cache is local and never contains auth headers."""
    def __init__(self, root, delay=1.0, reuse_cache=False, offline=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.delay = max(0.5, delay)
        self.reuse_cache = reuse_cache
        self.offline = offline
        self.last = 0.0
        self.dates = []

    def __call__(self, url, headers=()):
        """``headers`` are request headers a guide's own page sends, such as the
        ``XMLHttpRequest`` pair a JSON listing needs; they are never credentials
        and they take part in the cache key because they change the response."""
        headers = tuple(headers)
        key = hashlib.sha256("\n".join((url, *headers)).encode()).hexdigest()
        body_path = self.root / f"{key}.html.gz"
        meta_path = self.root / f"{key}.json"
        if (self.reuse_cache or self.offline) and body_path.exists() and meta_path.exists() and gzip.decompress(body_path.read_bytes()):
            meta = json.loads(meta_path.read_text())
            if meta["url"] != url:
                raise ValueError("Cache URL mismatch")
            self.dates.append(meta["fetched_at"][:10])
            return gzip.decompress(body_path.read_bytes()).decode("utf-8")
        if self.offline:
            raise ValueError(f"Missing cached page: {url}")
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                header_args = [argument for header in headers for argument in ("--header", header)]
                response = subprocess.run(
                    ["curl", "--fail", "--location", "--silent", "--show-error", "--max-time", "45",
                     *header_args, url],
                    capture_output=True, text=True, check=True,
                )
                body = response.stdout
                if not body.strip():
                    raise ValueError(f"Empty guide response: {url}")
                break
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                if attempt == 2:
                    raise ValueError(f"Could not fetch guide page: {url}") from error
                time.sleep(2 ** (attempt + 1))
        stamp = datetime.now(timezone.utc).isoformat()
        body_path.write_bytes(gzip.compress(body.encode(), mtime=0))
        meta_path.write_text(json.dumps({"url": url, "fetched_at": stamp}, indent=2) + "\n")
        self.dates.append(stamp[:10])
        return body


def render(entries, country="ES"):
    entries = sorted(entries, key=lambda row: (row["guide"], row["province"], row["locality"], row["name"]))
    return yaml.safe_dump({"format_version": 1, "country": country, "entries": entries}, sort_keys=False, allow_unicode=True)


def guide_crawlers(country, unresolved):
    """Bind each guide of ``country`` to a crawl taking (fetch, verified_at)."""
    from osusume.fifty_best_registry import crawl_fifty_best
    from osusume.hardens_registry import crawl_hardens
    from osusume.le_fooding_registry import crawl_le_fooding
    from osusume.macarfi_registry import crawl_macarfi
    from osusume.repsol_registry import crawl_repsol

    crawlers = {
        "michelin": lambda fetch, stamp: crawl_michelin(fetch, stamp, country),
        "michelin_bib": lambda fetch, stamp: crawl_michelin_bib(fetch, stamp, country),
        "repsol": lambda fetch, stamp: crawl_repsol(fetch, stamp),
        "fifty_best": lambda fetch, stamp: crawl_fifty_best(fetch, stamp, country, unresolved),
        "macarfi": lambda fetch, stamp: crawl_macarfi(fetch, stamp),
        "hardens": lambda fetch, stamp: crawl_hardens(fetch, stamp),
        "le_fooding": lambda fetch, stamp: crawl_le_fooding(fetch, stamp),
    }
    return [(guide, crawlers[guide]) for guide in COUNTRY_GUIDES[country]]


def compare_notes(entries, notes):
    """Report every missing/extra/changed Catalonia Michelin note, including spelling."""
    from osusume.guide_registry import normalized_text
    key = lambda value: normalized_text(value).replace(" ", "")
    actual = [row for row in entries if row["guide"] == "michelin" and row["province"] in {"Barcelona", "Girona", "Lleida", "Tarragona"}]
    correspondences = notes.get("name_correspondences", {})
    matched = set()
    differences = []
    for level, localities in notes.get("michelin", {}).items():
        for locality, names in localities.items():
            for name in names:
                official_name = correspondences.get(name, name)
                row = next((row for row in actual if key(row["name"]) == key(official_name) and key(row["locality"]) == key(locality)), None)
                if row is None:
                    differences.append(f"Missing from current starred listing: {name} ({locality}), notes {level} star(s).")
                    continue
                matched.add(row["url"])
                if row["level"] != level:
                    differences.append(f"Level differs: {name}, notes {level}; official {row['level']} ({row['url']}).")
                if row["name"] != name:
                    differences.append(f"Name differs: {name} → {row['name']} ({row['locality']}; {row['url']}).")
    for row in actual:
        if row["url"] not in matched:
            differences.append(f"Additional official entry: {row['name']} ({row['locality']}), {row['level']} star(s) ({row['url']}).")
    for name in notes.get("repsol", {}).get("soles", []):
        row = next((row for row in entries if row["guide"] == "repsol" and key(row["name"]) == key(name) and row["locality"] == "Barcelona"), None)
        if row is None or row["level"] != 1:
            differences.append(f"Repsol note differs: expected {name} (Barcelona), 1 Sol.")
    for name in notes.get("repsol", {}).get("recommended", []):
        if any(row["guide"] == "repsol" and key(row["name"]) == key(name) for row in entries):
            differences.append(f"Repsol note differs: {name} was Recommended in notes, now in Soles listing.")
    return differences


def merge_previous_locations(entries, path):
    """Carry a previous file's position fields onto the rows they belong to.

    ``scripts/enrich_registry_locations.py`` resolves coordinates the guides do
    not publish, so a rebuild from the same listings would otherwise drop them.
    A row is matched by guide and official URL, and only fields the fresh crawl
    did not produce are restored.
    """
    if not Path(path).exists():
        return 0
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    previous = {}
    for row in payload.get("entries") or []:
        if isinstance(row, dict) and row.get("guide") and row.get("url"):
            previous[(str(row["guide"]), str(row["url"]))] = row
    restored = 0
    for row in entries:
        older = previous.get((str(row.get("guide")), str(row.get("url"))))
        if not older:
            continue
        carried = {field: older[field] for field in ENRICHMENT_FIELDS
                   if field in older and field not in row}
        if "latitude" in row or "longitude" in row:
            carried.pop("latitude", None)
            carried.pop("longitude", None)
        if carried:
            row.update(carried)
            restored += 1
    return restored


def report_preserved(entries, path):
    """Print the per-guide count before and after, and any row the rebuild lost."""
    if not Path(path).exists():
        return
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    older = [row for row in (payload.get("entries") or []) if isinstance(row, dict)]
    if not older:
        return
    fresh = {(str(row.get("guide")), str(row.get("url"))) for row in entries}
    missing = [row for row in older if (str(row.get("guide")), str(row.get("url"))) not in fresh]
    print(f"registry rows: {len(older)} before, {len(entries)} after", file=sys.stderr)
    for row in missing:
        print(f"dropped: {row.get('guide')} {row.get('name')} ({row.get('locality')}) {row.get('url')}",
              file=sys.stderr)


def enrich_locations(entries):
    """Reuse Michelin coordinates only for uniquely named restaurants in the same town."""
    from osusume.guide_registry import names_match, normalized_text
    michelin = [row for row in entries if row["guide"] == "michelin"]
    for row in entries:
        if row["guide"] == "michelin" or "latitude" in row:
            continue
        matches = [other for other in michelin
                   if names_match(row["name"], other["name"])
                   and normalized_text(row["locality"]) == normalized_text(other["locality"])]
        if len(matches) == 1 and "latitude" in matches[0]:
            other = matches[0]
            row.update(latitude=other["latitude"], longitude=other["longitude"],
                       location_source_url=other["url"],
                       verified_at=min(row["verified_at"], other["verified_at"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", choices=sorted(COUNTRY_GUIDES), default="ES")
    parser.add_argument("--dry-run", action="store_true", help="crawl and cache, show diff without rewriting registry")
    parser.add_argument("--offline", action="store_true", help="rebuild from cached pages without advancing verified_at")
    parser.add_argument("--reuse-cache", action="store_true", help="resume interrupted crawl; preserve oldest cached verification date")
    parser.add_argument("--delay", type=float, default=1.0, help="minimum seconds between fetches (floor 0.5)")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    country = args.country
    output = args.output or ROOT / f"registry/{country.casefold()}_restaurants.yaml"
    unresolved: list[str] = []
    entries = []
    for guide, crawl in guide_crawlers(country, unresolved):
        fetch = Fetcher(ROOT / "registry/raw" / guide, args.delay, args.reuse_cache, args.offline)
        print(f"Reading {guide} official {country} listing and entries…", file=sys.stderr, flush=True)
        rows = crawl(fetch, datetime.now(timezone.utc).date().isoformat())
        for row in rows:
            row["verified_at"] = min(fetch.dates)
        entries.extend(rows)
        print(f"{guide}: {len(rows)} rated entries", file=sys.stderr, flush=True)
    restored = merge_previous_locations(entries, output)
    if restored:
        print(f"kept {restored} previously resolved locations", file=sys.stderr)
    enrich_locations(entries)
    report_preserved(entries, output)
    for note in unresolved:
        print(note, file=sys.stderr)
    notes_path = ROOT / "registry/catalonia_notes.yaml"
    if country == "ES" and notes_path.exists():
        for difference in compare_notes(entries, yaml.safe_load(notes_path.read_text())):
            print(difference, file=sys.stderr)
    result = render(entries, country)
    before = output.read_text() if output.exists() else ""
    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(before.splitlines(True), result.splitlines(True),
                                                fromfile=str(output), tofile="refreshed registry"))
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(".yaml.tmp")
        temporary.write_text(result)
        temporary.replace(output)
        print(f"Wrote {len(entries)} entries to {output}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Refresh failed; existing registry preserved: {error}")
