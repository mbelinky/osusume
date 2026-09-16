"""Harvest current Sol awards from Guía Repsol's official Spain listing."""

from __future__ import annotations

from datetime import date
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin


LISTING_URL = (
    "https://www.guiarepsol.com/es/comer/soles-repsol/"
    "ediciones-de-soles-guia-repsol/"
)
_BASE_URL = "https://www.guiarepsol.com"
_SOL_LEVELS = {"1": 1, "2": 2, "3": 3}


class RepsolListingError(ValueError):
    """The official listing did not contain a complete, consistent Sol set."""


def _classes(attributes: dict[str, str | None]) -> set[str]:
    return set((attributes.get("class") or "").split())


class _RepsolListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[dict[str, object]] = []
        self.expected_counts: dict[int, int] = {}
        self._div_depth = 0
        self._section_depth: int | None = None
        self._level: int | None = None
        self._entry: dict[str, object] | None = None
        self._capture_name = False
        self._name_parts: list[str] = []
        self._capture_count = False
        self._count_parts: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        classes = _classes(attributes)

        if tag == "div":
            self._div_depth += 1
            if "container-gene-result" in classes:
                self._level = _SOL_LEVELS.get(attributes.get("data-category", ""))
                self._section_depth = self._div_depth

        if self._level is None:
            return

        if tag == "span" and "result-count-num" in classes:
            self._capture_count = True
            self._count_parts = []
            return

        if tag == "li" and attributes.get("data-provincia") is not None:
            self._entry = {
                "locality": (attributes.get("data-localidad") or "").strip(),
                "province": (attributes.get("data-provincia") or "").strip(),
            }
            return

        if self._entry is None:
            return

        if tag == "a" and "galardonado-card" in classes:
            href = (attributes.get("href") or "").strip()
            title = (attributes.get("title") or "").strip()
            if href:
                self._entry["url"] = urljoin(_BASE_URL, href)
            if title:
                self._entry["name"] = title
        elif tag == "span" and "name" in classes:
            self._capture_name = True
            self._name_parts = []

    def handle_data(self, data: str) -> None:
        if self._capture_count:
            self._count_parts.append(data)
        if self._capture_name:
            self._name_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "span" and self._capture_count:
            self._capture_count = False
            count_text = "".join(self._count_parts).strip()
            if self._level is not None and count_text:
                try:
                    count = int(count_text)
                except ValueError as exc:
                    raise RepsolListingError(
                        f"invalid result count for {self._level} Sol: {count_text!r}"
                    ) from exc
                previous = self.expected_counts.setdefault(self._level, count)
                if previous != count:
                    raise RepsolListingError(
                        f"conflicting result counts for {self._level} Sol"
                    )

        if tag == "span" and self._capture_name:
            self._capture_name = False
            if self._entry is not None and "name" not in self._entry:
                self._entry["name"] = "".join(self._name_parts).strip()

        if tag == "li" and self._entry is not None:
            self._entry["level"] = self._level
            self.entries.append(self._entry)
            self._entry = None
            self._capture_name = False

        if tag == "div":
            if self._section_depth == self._div_depth:
                self._section_depth = None
                self._level = None
            self._div_depth -= 1


def _verified_date(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("verified_at must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("verified_at must be an ISO date (YYYY-MM-DD)") from exc
    if parsed.isoformat() != value:
        raise ValueError("verified_at must be an ISO date (YYYY-MM-DD)")
    return value


def _parse_listing(page: str, verified_at: str) -> list[dict]:
    parser = _RepsolListingParser()
    parser.feed(page)
    parser.close()

    if set(parser.expected_counts) != {1, 2, 3}:
        missing = sorted({1, 2, 3} - set(parser.expected_counts))
        raise RepsolListingError(f"listing is missing Sol sections: {missing}")

    rows: list[dict] = []
    seen: set[str] = set()
    actual_counts = {1: 0, 2: 0, 3: 0}
    required = ("name", "locality", "province", "url", "level")
    for entry in parser.entries:
        missing = [field for field in required if not entry.get(field)]
        if missing:
            raise RepsolListingError(
                f"incomplete restaurant entry; missing {', '.join(missing)}"
            )
        url = str(entry["url"])
        if url in seen:
            raise RepsolListingError(f"duplicate restaurant URL: {url}")
        seen.add(url)
        level = int(entry["level"])
        actual_counts[level] += 1
        rows.append(
            {
                "name": str(entry["name"]),
                "locality": str(entry["locality"]),
                "province": str(entry["province"]),
                "guide": "repsol",
                "level": level,
                "url": url,
                "verified_at": verified_at,
            }
        )

    for level, expected in parser.expected_counts.items():
        actual = actual_counts[level]
        if actual != expected:
            raise RepsolListingError(
                f"{level} Sol section declares {expected} restaurants but contains {actual}"
            )

    return rows


def crawl_repsol(fetch: Callable[[str], str], verified_at: str) -> list[dict]:
    """Return every current 1, 2, and 3 Sol restaurant in the official listing.

    ``fetch`` owns transport, caching, and rate limiting and must return decoded
    text for the requested URL. The listing is deliberately fetched once.
    """

    stamp = _verified_date(verified_at)
    page = fetch(LISTING_URL)
    if not isinstance(page, str):
        raise TypeError("fetch must return str")
    return _parse_listing(page, stamp)
