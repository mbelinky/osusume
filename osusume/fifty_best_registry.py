"""Read the current World's 50 Best Restaurants list and sort it by country."""
from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlparse

from .guide_registry import normalized_text

BASE = "https://www.theworlds50best.com"
LIST_URL = BASE + "/list/1-50"
SITEMAP_URL = BASE + "/sitemap.xml"
BLOCKS = ("1-50", "51-100")
# The official list page carries both halves; each block declares fifty places.
BLOCK_RANKS = {"1-50": range(1, 51), "51-100": range(51, 101)}
# Country names as the official site writes them, mapped to ISO country codes.
COUNTRY_CODES = {
    "spain": "ES",
    "espana": "ES",
    "uk": "GB",
    "united kingdom": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "france": "FR",
}
_DISCOVERY_PATH = "/discovery/Establishments/"


class FiftyBestListError(ValueError):
    """The official list did not contain a complete, consistent 1-100 ranking."""


def level_for_rank(rank: int) -> int:
    if 1 <= rank <= 10:
        return 3
    if rank <= 50:
        return 2
    if rank <= 100:
        return 1
    raise FiftyBestListError(f"rank {rank} is outside the published list")


class _ListParser(HTMLParser):
    """Collect rank, name, city and profile link for every ranked list item."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: dict[str, list[dict]] = {}
        self._div_depth = 0
        self._block: str | None = None
        self._block_depth: int | None = None
        self._item: dict | None = None
        self._item_depth: int | None = None
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div":
            self._div_depth += 1
            listed = attributes.get("data-list")
            if listed in BLOCKS and "list-grid" in classes:
                self._block = listed
                self._block_depth = self._div_depth
                self.blocks.setdefault(listed, [])
            elif self._block is not None and "list-item" in classes and self._item is None:
                self._item = {"rank": None, "name": "", "city": "", "url": ""}
                self._item_depth = self._div_depth
                self.blocks[self._block].append(self._item)
        if self._item is None:
            return
        if tag == "a":
            href = (attributes.get("href") or "").strip()
            if "/the-list/" in href and not self._item["url"]:
                self._item["url"] = BASE + href if href.startswith("/") else href
        elif tag == "p":
            self._capture = "rank" if "rank" in classes else "city"
            self._parts = []
        elif tag == "h2":
            self._capture = "name"
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._item is not None and tag in {"p", "h2"} and self._capture:
            text = "".join(self._parts).strip()
            if self._capture == "rank" and text.isdigit() and self._item["rank"] is None:
                self._item["rank"] = int(text)
            elif self._capture == "name" and not self._item["name"]:
                self._item["name"] = text
            elif self._capture == "city" and text and not self._item["city"]:
                self._item["city"] = text
            self._capture = None
        if tag == "div":
            if self._item_depth == self._div_depth:
                self._item = None
                self._item_depth = None
            if self._block_depth == self._div_depth:
                self._block = None
                self._block_depth = None
            self._div_depth -= 1


def parse_list(page: str) -> list[dict]:
    """Return the full 1-100 ranking, or refuse a page that does not carry it."""
    parser = _ListParser()
    parser.feed(page)
    parser.close()
    rows: list[dict] = []
    for block, ranks in BLOCK_RANKS.items():
        items = parser.blocks.get(block)
        if items is None:
            raise FiftyBestListError(f"official list page has no {block} block; refusing refresh")
        found = {item["rank"] for item in items if item["rank"] is not None}
        if found != set(ranks):
            raise FiftyBestListError(f"{block} block does not rank {ranks.start}-{ranks.stop - 1}")
        for item in items:
            if not item["name"] or not item["city"]:
                raise FiftyBestListError(f"incomplete list entry at rank {item['rank']}")
            rows.append({**item, "level": level_for_rank(item["rank"])})
    return sorted(rows, key=lambda row: row["rank"])


def parse_sitemap(page: str) -> dict[str, list[dict]]:
    """Index the official establishment directory by restaurant name."""
    index: dict[str, list[dict]] = {}
    for location in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", page):
        path = urlparse(location).path
        if _DISCOVERY_PATH not in path:
            continue
        parts = path.split(_DISCOVERY_PATH, 1)[1].split("/")
        if len(parts) != 3 or not parts[2].endswith(".html"):
            continue
        country, city, page_name = parts
        name = page_name[: -len(".html")].replace("-", " ")
        index.setdefault(normalized_text(name), []).append(
            {
                "country": COUNTRY_CODES.get(normalized_text(country), ""),
                "city": city.replace("-", " "),
                "url": BASE + path,
            }
        )
    if not index:
        raise FiftyBestListError("official sitemap lists no establishments; refusing refresh")
    return index


def parse_profile_country(page: str) -> tuple[str, str]:
    """Return (country code, locality) from a list profile page's JSON-LD address."""
    for raw in re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', page, re.S):
        data = json.loads(unescape(raw))
        for item in data if isinstance(data, list) else [data]:
            if item.get("@type") != "Restaurant":
                continue
            address = item.get("address") or {}
            street = str(address.get("streetAddress", ""))
            tail = normalized_text(street.split(",")[-1]) if "," in street else ""
            if not tail:
                raise FiftyBestListError("profile address has no country; refusing refresh")
            return COUNTRY_CODES.get(tail, ""), str(address.get("addressLocality", ""))
    raise FiftyBestListError("profile page has no Restaurant JSON-LD; refusing refresh")


def _confirms_city(page: str, city: str) -> bool:
    location = re.search(r'<p class="location"[^>]*>(.*?)</p>', page, re.S)
    if not location:
        raise FiftyBestListError("establishment page has no location; refusing refresh")
    return normalized_text(city) in normalized_text(location.group(1))


def crawl_fifty_best(
    fetch: Callable[[str], str],
    verified_at: str,
    country: str = "ES",
    unresolved: list[str] | None = None,
) -> list[dict]:
    """Return the ranked restaurants the official site places in ``country``.

    The list page gives rank, name and city only, so each country comes from the
    guide itself: the establishment directory in the official sitemap, confirmed
    by the restaurant's own page whenever the directory city differs.
    """
    rows = parse_list(fetch(LIST_URL))
    directory = parse_sitemap(fetch(SITEMAP_URL))
    notes = unresolved if unresolved is not None else []
    entries = []
    for row in rows:
        listed = directory.get(normalized_text(row["name"]), [])
        exact = [item for item in listed if normalized_text(item["city"]) == normalized_text(row["city"])]
        candidates = [item for item in listed if item["country"] == country]
        resolved = ""
        locality = row["city"]
        url = row["url"]
        if len(exact) == 1:
            resolved = exact[0]["country"]
            url = url or exact[0]["url"]
        elif row["url"]:
            resolved, profile_locality = parse_profile_country(fetch(row["url"]))
            locality = profile_locality or locality
        elif len(candidates) == 1 and _confirms_city(fetch(candidates[0]["url"]), row["city"]):
            resolved = candidates[0]["country"]
            url = candidates[0]["url"]
        if resolved != country:
            if not resolved and (candidates or not listed):
                notes.append(
                    f"fifty_best: rank {row['rank']} {row['name']} ({row['city']}) "
                    f"has no official page that places it in a country; skipped"
                )
            continue
        entries.append(
            {
                "name": row["name"],
                "locality": locality,
                "province": row["city"],
                "guide": "fifty_best",
                "level": row["level"],
                "rank": row["rank"],
                "url": url,
                "verified_at": verified_at,
            }
        )
    return entries
