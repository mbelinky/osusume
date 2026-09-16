"""Read Guía Macarfi's official city listings through their own JSON responses.

Macarfi renders its restaurant listing from a Laravel paginator that serves
JSON to the page's own XHR. Asking for that JSON is the same public listing the
browser shows, so the crawl reads ``data[]`` page by page and refuses anything
that does not add up to the paginator's declared total.
"""
from __future__ import annotations

import json
from typing import Callable

BASE = "https://macarfi.com"
# Headers the listing's own XHR sends; without them the route returns HTML.
JSON_HEADERS = ("X-Requested-With: XMLHttpRequest", "Accept: application/json")
# City code in the listing path, with the province the code stands for.
CITIES = {"bcn": "Barcelona", "mad": "Madrid"}
LISTING = BASE + "/es/{city}/restaurantes"
DETAIL = BASE + "/es/{city}/ficha-restaurante/{slug}"
# Macarfi scores out of 10; only rated restaurants enter the registry.
LEVEL_THRESHOLDS = ((9.0, 3), (8.0, 2), (7.0, 1))
MINIMUM_RATING = 7.0
# A runaway paginator must stop rather than walk the site forever.
MAX_PAGES = 200


class MacarfiListingError(ValueError):
    """The official listing did not serve a complete, consistent page set."""


def level_for_rating(rating: float) -> int | None:
    """Return the registry level for a Macarfi rating, or None below 7."""
    for threshold, level in LEVEL_THRESHOLDS:
        if rating >= threshold:
            return level
    return None


def parse_rating(value: object) -> float | None:
    """Macarfi writes ratings with a Spanish decimal comma, e.g. ``"9,7"``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_page(body: str, city: str) -> dict:
    """Return one paginator page as ``{page, last_page, total, per_page, data}``."""
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as error:
        raise MacarfiListingError(f"Macarfi {city} listing is not JSON; refusing refresh") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MacarfiListingError(f"Macarfi {city} listing has no data array; refusing refresh")
    numbers = {}
    for field in ("current_page", "last_page", "total", "per_page"):
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise MacarfiListingError(f"Macarfi {city} listing has no {field}; refusing refresh")
        numbers[field] = value
    return {**numbers, "data": payload["data"]}


def _coordinate(value: object, limit: float) -> float | None:
    number = parse_rating(value)
    if number is None or not -limit <= number <= limit:
        return None
    return number


def _row(record: object, city: str, province: str, verified_at: str) -> dict | None:
    if not isinstance(record, dict):
        raise MacarfiListingError(f"Macarfi {city} listing holds a non-object entry; refusing refresh")
    name = str(record.get("name") or "").strip()
    slug = str(record.get("slug") or "").strip()
    location = record.get("location") or {}
    locality = str((location or {}).get("name") or "").strip()
    listed_province = str(((location or {}).get("province") or {}).get("name") or "").strip()
    if not name or not slug or not locality or not listed_province:
        raise MacarfiListingError(
            f"Macarfi {city} entry is missing name, slug or location; refusing refresh"
        )
    rating = parse_rating(record.get("rating"))
    if rating is None or rating < MINIMUM_RATING:
        return None
    level = level_for_rating(rating)
    if level is None:  # pragma: no cover - MINIMUM_RATING already excluded these
        return None
    row = {
        "name": name,
        "locality": locality,
        "province": listed_province or province,
        "guide": "macarfi",
        "level": level,
        "rating": rating,
        "url": DETAIL.format(city=city, slug=slug),
        "verified_at": verified_at,
    }
    address = str(record.get("address") or "").strip()
    if address:
        row["address"] = address
    latitude = _coordinate(record.get("latitude"), 90.0)
    longitude = _coordinate(record.get("longitude"), 180.0)
    if latitude is not None and longitude is not None:
        row.update(latitude=latitude, longitude=longitude)
    return row


def crawl_macarfi(fetch: Callable[..., str], verified_at: str) -> list[dict]:
    """Return every restaurant Macarfi rates 7 or better in its seeded cities.

    ``fetch`` owns transport, caching and rate limiting; it must accept the
    listing's own XHR headers and return the decoded response body.
    """
    entries: list[dict] = []
    seen: set[str] = set()
    for city, province in CITIES.items():
        listing = LISTING.format(city=city)
        page_number = 1
        declared: tuple[int, int] | None = None
        collected = 0
        while True:
            page = parse_page(fetch(f"{listing}?page={page_number}", headers=JSON_HEADERS), city)
            if page["current_page"] != page_number:
                raise MacarfiListingError(
                    f"Macarfi {city} served page {page['current_page']} for page {page_number}"
                )
            if declared is None:
                declared = (page["total"], page["last_page"])
                if not declared[0] or not declared[1]:
                    raise MacarfiListingError(f"Macarfi {city} listing is empty; refusing refresh")
            elif declared != (page["total"], page["last_page"]):
                raise MacarfiListingError(f"Macarfi {city} total changed during pagination; retry refresh")
            if not page["data"] and page_number <= declared[1]:
                raise MacarfiListingError(f"Macarfi {city} page {page_number} is empty; refusing refresh")
            collected += len(page["data"])
            for record in page["data"]:
                row = _row(record, city, province, verified_at)
                if row is None:
                    continue
                if row["url"] in seen:
                    raise MacarfiListingError(f"Macarfi duplicate restaurant URL: {row['url']}")
                seen.add(row["url"])
                entries.append(row)
            if page_number >= declared[1]:
                break
            page_number += 1
            if page_number > MAX_PAGES:
                raise MacarfiListingError(f"Macarfi {city} listing exceeds {MAX_PAGES} pages; refusing refresh")
        if collected != declared[0]:
            raise MacarfiListingError(
                f"Macarfi {city} pagination incomplete: {collected} of {declared[0]}"
            )
    return entries
