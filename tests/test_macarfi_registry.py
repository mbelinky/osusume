"""Offline cover for the Guía Macarfi JSON listing crawl."""
import json

import pytest

from osusume.macarfi_registry import (
    CITIES,
    JSON_HEADERS,
    LISTING,
    MacarfiListingError,
    crawl_macarfi,
    level_for_rating,
    parse_page,
    parse_rating,
)

PER_PAGE = 2


def record(identifier: int, name: str, slug: str, rating: str | None, locality: str, province: str) -> dict:
    return {
        "id": identifier,
        "name": name,
        "slug": slug,
        "address": f"Calle {identifier}, 08036 {province}",
        "location": {"id": identifier, "name": locality, "province": {"id": 1, "name": province, "code": "bcn"}},
        "cooking_rate": rating,
        "latitude": "41.387800",
        "longitude": "2.153200",
        "rating": rating,
    }


def page(records: list[dict], *, current: int, last: int, total: int, **overrides) -> str:
    payload = {
        "current_page": current,
        "last_page": last,
        "total": total,
        "per_page": PER_PAGE,
        "data": records,
    }
    payload.update(overrides)
    return json.dumps(payload)


BARCELONA = [
    [record(1, "Disfrutar", "disfrutar", "9,7", "L'Eixample Esquerre", "Barcelona"),
     record(2, "Lasarte", "lasarte", "9,4", "L'Eixample Dret", "Barcelona")],
    [record(3, "Mediocre", "mediocre", "6,9", "Gràcia", "Barcelona"),
     record(4, "Solid", "solid", "8,0", "Gràcia", "Barcelona")],
]
MADRID = [
    [record(5, "DiverXO", "diverxo", "9,1", "Chamartín", "Madrid"),
     record(6, "Correct", "correct", "7,0", "Centro", "Madrid")],
]


def site(barcelona=None, madrid=None) -> dict[str, str]:
    barcelona = BARCELONA if barcelona is None else barcelona
    madrid = MADRID if madrid is None else madrid
    pages = {}
    for city, blocks in (("bcn", barcelona), ("mad", madrid)):
        total = sum(len(block) for block in blocks)
        for index, block in enumerate(blocks, start=1):
            pages[f"{LISTING.format(city=city)}?page={index}"] = page(
                block, current=index, last=len(blocks), total=total
            )
    return pages


def fetch_from(pages: dict[str, str]):
    def fetch(url: str, headers=()) -> str:
        assert tuple(headers) == JSON_HEADERS, "the listing only serves JSON to its own XHR headers"
        if url not in pages:
            raise ValueError(f"unexpected fetch: {url}")
        return pages[url]

    return fetch


def test_rating_maps_to_guide_level():
    assert [level_for_rating(value) for value in (10.0, 9.0, 8.9, 8.0, 7.0)] == [3, 3, 2, 2, 1]
    assert level_for_rating(6.9) is None


def test_spanish_decimal_comma_is_read_as_a_number():
    assert parse_rating("9,7") == 9.7
    assert parse_rating(8) == 8.0
    assert parse_rating(None) is None
    assert parse_rating("sin nota") is None


def test_crawl_keeps_rated_restaurants_and_drops_the_rest():
    rows = crawl_macarfi(fetch_from(site()), "2026-09-16")

    assert [row["name"] for row in rows] == ["Disfrutar", "Lasarte", "Solid", "DiverXO", "Correct"]
    disfrutar = rows[0]
    assert disfrutar["guide"] == "macarfi"
    assert disfrutar["level"] == 3
    assert disfrutar["locality"] == "L'Eixample Esquerre"
    assert disfrutar["province"] == "Barcelona"
    assert disfrutar["url"] == "https://macarfi.com/es/bcn/ficha-restaurante/disfrutar"
    assert (disfrutar["latitude"], disfrutar["longitude"]) == (41.3878, 2.1532)
    assert disfrutar["verified_at"] == "2026-09-16"
    assert [row["level"] for row in rows] == [3, 3, 2, 3, 1]


def test_both_seeded_cities_are_crawled():
    rows = crawl_macarfi(fetch_from(site()), "2026-09-16")

    assert {row["url"].split("/es/")[1].split("/")[0] for row in rows} == set(CITIES)


def test_listing_that_is_not_json_is_refused():
    pages = site()
    pages[f"{LISTING.format(city='bcn')}?page=1"] = "<html>Access denied</html>"

    with pytest.raises(MacarfiListingError, match="not JSON"):
        crawl_macarfi(fetch_from(pages), "2026-09-16")


def test_listing_without_a_declared_total_is_refused():
    with pytest.raises(MacarfiListingError, match="no total"):
        parse_page(json.dumps({"current_page": 1, "last_page": 1, "per_page": 2, "data": []}), "bcn")


def test_page_that_serves_another_page_number_is_refused():
    pages = site()
    pages[f"{LISTING.format(city='bcn')}?page=2"] = page(
        BARCELONA[1], current=1, last=2, total=4
    )

    with pytest.raises(MacarfiListingError, match="served page 1 for page 2"):
        crawl_macarfi(fetch_from(pages), "2026-09-16")


def test_total_that_changes_during_pagination_is_refused():
    pages = site()
    pages[f"{LISTING.format(city='bcn')}?page=2"] = page(
        BARCELONA[1], current=2, last=2, total=9
    )

    with pytest.raises(MacarfiListingError, match="total changed"):
        crawl_macarfi(fetch_from(pages), "2026-09-16")


def test_short_pagination_is_refused():
    pages = site()
    pages[f"{LISTING.format(city='bcn')}?page=2"] = page([], current=2, last=2, total=4)

    with pytest.raises(MacarfiListingError, match="page 2 is empty"):
        crawl_macarfi(fetch_from(pages), "2026-09-16")


def test_page_that_loses_restaurants_is_refused():
    pages = site()
    pages[f"{LISTING.format(city='bcn')}?page=2"] = page(
        BARCELONA[1][:1], current=2, last=2, total=4
    )

    with pytest.raises(MacarfiListingError, match="pagination incomplete: 3 of 4"):
        crawl_macarfi(fetch_from(pages), "2026-09-16")


def test_entry_without_a_name_or_location_is_refused():
    broken = dict(BARCELONA[0][0])
    broken["location"] = {"name": "", "province": {"name": ""}}

    with pytest.raises(MacarfiListingError, match="missing name, slug or location"):
        crawl_macarfi(fetch_from(site(barcelona=[[broken]])), "2026-09-16")


def test_duplicate_restaurant_is_refused():
    duplicate = [BARCELONA[0][0], dict(BARCELONA[0][0])]

    with pytest.raises(MacarfiListingError, match="duplicate restaurant URL"):
        crawl_macarfi(fetch_from(site(barcelona=[duplicate])), "2026-09-16")
