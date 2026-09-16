"""Read Le Fooding's official restaurant guide and keep its Paris selection.

Le Fooding publishes a selection, not a rating: a restaurant is either in the
guide or it is not. Every selected Paris address therefore enters the registry
at level 1, the registry's lowest tier, and the crawl refuses a run that loses
pages or falls far below the guide's published size.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Callable

BASE = "https://lefooding.com"
LISTING = BASE + "/restaurants"
# The guide renders a fixed grid; a short page can only be the last one.
CARDS_PER_PAGE = 16
# Le Fooding also covers Belgium, so Paris is selected by French postcode.
PARIS_POSTCODE = re.compile(r"^75\d{3}$")
PARIS_LOCALITY = "Paris"
PARIS_PROVINCE = "Île-de-France"
# The whole guide has held well over a thousand addresses; far less means a
# broken crawl rather than a shrunken guide.
MINIMUM_TOTAL = 500
MAX_PAGES = 200


class LeFoodingListingError(ValueError):
    """The official listing did not serve a complete, consistent page set."""


class _CardParser(HTMLParser):
    """Collect slug, name, editorial tag and address for every guide card."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict] = []
        self._card: dict | None = None
        self._depth = 0
        self._capture: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "article":
            self._depth += 1
            if "cardGuide" in classes and self._card is None:
                self._card = {"name": "", "slug": "", "label": "", "address": ""}
                self.cards.append(self._card)
            return
        if self._card is None:
            return
        if tag == "br" and self._capture == "address":
            self._parts.append("\n")
        elif tag == "a" and not self._card["slug"]:
            href = (attributes.get("href") or "").strip()
            match = re.fullmatch(r"/restaurants/([^/?#]+)", href)
            if match:
                self._card["slug"] = match.group(1)
        elif "cardGuide__title" in classes:
            self._capture, self._parts = "name", []
        elif "cardGuide__label" in classes:
            self._capture, self._parts = "label", []
        elif "cardGuide__address" in classes:
            self._capture, self._parts = "address", []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._card is not None and self._capture and tag in {"h2", "h3", "p", "span", "div"}:
            text = "\n".join(" ".join(line.split()) for line in "".join(self._parts).split("\n"))
            self._card[self._capture] = text.strip("\n ")
            self._capture = None
        if tag == "article":
            self._depth = max(0, self._depth - 1)
            if self._depth == 0:
                self._card = None


def parse_address(text: str) -> tuple[str, str, str]:
    """Return (street, locality, postcode) from a card's address block."""
    lines = [line for line in (part.strip() for part in text.split("\n")) if line]
    if not lines:
        return "", "", ""
    tail = lines[-1]
    postcode = ""
    match = re.search(r"\((\d{4,5})\)\s*$", tail)
    if match:
        postcode = match.group(1)
        tail = tail[: match.start()].strip()
    locality = tail or (lines[-2] if len(lines) > 1 else "")
    street_lines = lines[:-1] if len(lines) > 1 else []
    if not tail and street_lines:
        street_lines = street_lines[:-1]
    return " ".join(street_lines), locality, postcode


def parse_page(page: str) -> list[dict]:
    """Return every guide card on one listing page."""
    parser = _CardParser()
    parser.feed(page)
    parser.close()
    cards = []
    for card in parser.cards:
        if not card["name"] or not card["slug"]:
            raise LeFoodingListingError("Le Fooding card has no name or link; refusing refresh")
        street, locality, postcode = parse_address(card["address"])
        cards.append({**card, "street": street, "locality": locality, "postcode": postcode})
    return cards


def crawl_le_fooding(fetch: Callable[..., str], verified_at: str) -> list[dict]:
    """Return Le Fooding's selected Paris restaurants as level 1 registry rows.

    The whole guide is walked once because the listing carries no city filter;
    only addresses with a Paris postcode are kept.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    total = 0
    page_number = 1
    short_page: int | None = None
    while page_number <= MAX_PAGES:
        cards = parse_page(fetch(f"{LISTING}?page={page_number}"))
        if not cards:
            break
        if short_page is not None:
            raise LeFoodingListingError(
                f"Le Fooding page {short_page} was short but page {page_number} still has cards; refusing refresh"
            )
        if len(cards) < CARDS_PER_PAGE:
            short_page = page_number
        elif len(cards) > CARDS_PER_PAGE:
            raise LeFoodingListingError(
                f"Le Fooding page {page_number} has {len(cards)} cards, expected {CARDS_PER_PAGE}"
            )
        total += len(cards)
        for card in cards:
            if not PARIS_POSTCODE.fullmatch(card["postcode"]):
                continue
            url = f"{BASE}/restaurants/{card['slug']}"
            if url in seen:
                raise LeFoodingListingError(f"Le Fooding duplicate restaurant URL: {url}")
            seen.add(url)
            row = {
                "name": card["name"],
                "locality": PARIS_LOCALITY,
                "province": PARIS_PROVINCE,
                "guide": "le_fooding",
                "level": 1,
                "url": url,
                "verified_at": verified_at,
            }
            if card["street"]:
                row["address"] = card["street"]
            row["postal_code"] = card["postcode"]
            if card["label"]:
                row["guide_category"] = card["label"]
            entries.append(row)
        page_number += 1
    else:
        raise LeFoodingListingError(f"Le Fooding listing exceeds {MAX_PAGES} pages; refusing refresh")
    if total < MINIMUM_TOTAL:
        raise LeFoodingListingError(
            f"Le Fooding listing served {total} restaurants, below {MINIMUM_TOTAL}; refusing refresh"
        )
    if not entries:
        raise LeFoodingListingError("Le Fooding listing holds no Paris addresses; refusing refresh")
    return entries
