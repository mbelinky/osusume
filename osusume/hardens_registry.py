"""Read Harden's official Top 100 UK Restaurants ranking."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urlparse

BASE = "https://www.hardens.com"
LISTING = BASE + "/top-100-uk-restaurants/"
# The ranking is published as a fixed hundred; anything shorter is a broken page.
RANKS = range(1, 101)
# Rank bands Harden's itself prints on the list, mapped to registry levels.
LEVEL_BANDS = ((20, 3), (50, 2), (100, 1))
# UK postcode areas that make up London; the listing's links carry the prefix.
LONDON_POSTCODE_AREAS = {"E", "EC", "N", "NW", "SE", "SW", "W", "WC"}
GREATER_LONDON = "Greater London"
# Words a British place name keeps lowercase inside the name.
_LOWERCASE_WORDS = {"in", "on", "upon", "the", "of", "under", "by", "and", "le", "la"}
_LINK_PATH = "/az/restaurants/"


class HardensListingError(ValueError):
    """The official ranking page did not contain a complete 1-100 list."""


def level_for_rank(rank: int) -> int:
    for limit, level in LEVEL_BANDS:
        if rank <= limit:
            return level
    raise HardensListingError(f"rank {rank} is outside the published Top 100")


def town_name(slug: str) -> str:
    words = [word for word in slug.replace("_", "-").split("-") if word]
    if not words:
        raise HardensListingError("Harden's link has no town; refusing refresh")
    return " ".join(
        word if index and word in _LOWERCASE_WORDS else word[:1].upper() + word[1:]
        for index, word in enumerate(words)
    )


def postcode_area(prefix: str) -> str:
    area = re.match(r"[A-Za-z]+", prefix.strip())
    if not area:
        raise HardensListingError(f"Harden's link has no postcode prefix: {prefix!r}")
    return area.group(0).upper()


def _place(link: str) -> tuple[str, str]:
    """Return (locality, province) from the restaurant's own listing path."""
    path = urlparse(link).path
    if _LINK_PATH not in path:
        raise HardensListingError(f"Harden's link is not a restaurant page: {link}")
    parts = [part for part in path.split(_LINK_PATH, 1)[1].split("/") if part]
    if len(parts) != 3:
        raise HardensListingError(f"Harden's link has no town and postcode: {link}")
    town, prefix, _slug = parts
    if postcode_area(prefix) in LONDON_POSTCODE_AREAS:
        return "London", GREATER_LONDON
    name = town_name(town)
    return name, name


class _RankingParser(HTMLParser):
    """Collect rank, name and official link for every ranked article."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[dict] = []
        self._item: dict | None = None
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article" and "gallery-hover" in classes:
            link = (attributes.get("attrib-link") or "").strip()
            if not link:
                raise HardensListingError("Harden's ranking article has no link; refusing refresh")
            self._item = {"rank": None, "name": "", "url": link}
            self.items.append(self._item)
            return
        if self._item is None or tag not in {"h1", "h2", "span", "div", "p"}:
            return
        if "article-number" in classes:
            self._capture, self._parts = "rank", []
        elif "article-title" in classes:
            self._capture, self._parts = "name", []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._capture and tag in {"h1", "h2", "span", "div", "p"}:
            text = " ".join("".join(self._parts).split())
            if self._item is not None:
                if self._capture == "rank" and text.isdigit() and self._item["rank"] is None:
                    self._item["rank"] = int(text)
                elif self._capture == "name" and not self._item["name"]:
                    self._item["name"] = text
            self._capture = None
        elif tag == "article":
            self._item = None


def parse_ranking(page: str, verified_at: str) -> list[dict]:
    """Return the whole Top 100, or refuse a page that does not carry it."""
    parser = _RankingParser()
    parser.feed(page)
    parser.close()
    complete = [item for item in parser.items if item["rank"] is not None and item["name"]]
    if len(complete) < len(RANKS):
        raise HardensListingError(
            f"Harden's Top 100 parsed {len(complete)} of {len(RANKS)} entries; refusing refresh"
        )
    if {item["rank"] for item in complete} != set(RANKS):
        raise HardensListingError("Harden's ranking does not run 1-100; refusing refresh")
    rows = []
    for item in sorted(complete, key=lambda item: item["rank"]):
        locality, province = _place(item["url"])
        rows.append(
            {
                "name": item["name"],
                "locality": locality,
                "province": province,
                "guide": "hardens",
                "level": level_for_rank(item["rank"]),
                "rank": item["rank"],
                "url": item["url"],
                "verified_at": verified_at,
            }
        )
    return rows


def crawl_hardens(fetch: Callable[..., str], verified_at: str) -> list[dict]:
    """Return Harden's current Top 100 UK Restaurants as registry rows.

    The ranking is one server-rendered page, so it is deliberately fetched once.
    """
    return parse_ranking(fetch(LISTING), verified_at)
