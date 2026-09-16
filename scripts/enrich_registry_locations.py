#!/usr/bin/env python3
"""Add Places coordinates to guide registry rows that have none.

Guide listings such as Repsol give a locality but no position, so those rows
cannot be scoped by distance and are the last to be injected. This resolves
each such row once through ``goplaces search`` (one polite call per row),
keeps the match only when the registry identity rules accept it, and writes
``latitude``, ``longitude``, ``place_id`` and ``location_source: places``.
The guide fields are never changed. Needs GOOGLE_PLACES_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from osusume.guide_registry import registry_entry_matches_candidate  # noqa: E402


def lookup(entry: dict, country: str) -> dict | None:
    query = f"{entry['name']}, {entry['locality']}, {entry['province']}"
    command = ["goplaces", "search", query, "--limit", "1", "--region", country, "--type", "restaurant", "--json"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"goplaces exit {completed.returncode}")
    payload = json.loads(completed.stdout or "{}")
    rows = payload.get("results") or payload.get("places") or (payload if isinstance(payload, list) else [])
    return rows[0] if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", required=True, help="ISO2 code, selects registry/<cc>_restaurants.yaml")
    parser.add_argument("--province", action="append", default=[], help="only rows in these provinces (repeatable)")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many lookups (0 = all)")
    parser.add_argument("--delay", type=float, default=0.3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = ROOT / "registry" / f"{args.country.lower()}_restaurants.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = payload["entries"]
    pending = [
        row for row in entries
        if row.get("latitude") is None and (not args.province or row.get("province") in args.province)
    ]
    if args.limit:
        pending = pending[: args.limit]
    resolved = skipped = 0
    for row in pending:
        place = lookup(row, args.country.upper())
        time.sleep(args.delay)
        if not place or not registry_entry_matches_candidate(row, {**place, "name": place.get("name", "")}):
            skipped += 1
            print(f"skip  {row['name']} ({row['locality']}) -> {place.get('name') if place else 'no result'}", file=sys.stderr)
            continue
        location = place.get("location") or {}
        row["latitude"] = float(location.get("lat", location.get("latitude")))
        row["longitude"] = float(location.get("lng", location.get("longitude")))
        row["place_id"] = str(place.get("place_id") or place.get("id"))
        row["location_source"] = "places"
        resolved += 1
        print(f"ok    {row['name']} ({row['locality']}) -> {place.get('name')}", file=sys.stderr)
    print(f"{resolved} resolved, {skipped} skipped, {len(pending)} attempted", file=sys.stderr)
    if args.dry_run or not resolved:
        return 0
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    temporary = path.with_suffix(".yaml.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
