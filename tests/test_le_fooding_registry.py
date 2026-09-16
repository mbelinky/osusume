"""Offline cover for the Le Fooding Paris selection crawl."""
import pytest

from osusume.le_fooding_registry import (
    CARDS_PER_PAGE,
    LISTING,
    LeFoodingListingError,
    crawl_le_fooding,
    parse_address,
    parse_page,
)

PARIS_PAGES = 33  # enough full pages to clear the crawl's minimum size


def card(name: str, slug: str, label: str, street: str, city: str, postcode: str) -> str:
    return (
        '<li><article class="card o-card cardGuide paginatedList__card o-card--restaurant">'
        f'<a href="/restaurants/{slug}" class="o-card__container">'
        '<section class="o-card__picture"><picture><img alt="" class="nuxt-img"></picture></section> '
        '<section class="o-card__content">'
        f'<p class="cardGuide__label o-card__label">{label}</p> '
        '<section class="cardGuide__content">'
        f'<h2 class="cardGuide__title e-uppercase">{name}</h2> '
        f'<p class="cardGuide__address e-geo-h2"><!---->{street}<br>\n        {city}\n'
        f'        ({postcode})\n      </p> <!----></section></section></a></article></li>'
    )


def paris(index: int) -> str:
    return card(f"Paris {index}", f"paris-{index}", "Bistrot", f"{index} Rue de Test", "Paris", "75011")


def page(cards: list[str]) -> str:
    return '<ul class="paginatedList">' + "".join(cards) + "</ul>"


NAMED = [
    card("Septime", "septime", "Gastro", "80 Rue de Charonne", "Paris", "75011"),
    card("Le Chateaubriand", "le-chateaubriand", "Gastro", "129 Av. Parmentier", "Paris", "75011"),
    card("Drip Cà Phê", "drip-ca-phe", "Lèche-doigts", "Rue des Poissonniers 13", "Bruxelles", "1000"),
    card("Belle Maria", "belle-maria", "Bistrot", "9 Rte du Ris", "Douarnenez", "29100"),
]


def site(*, last_page_cards: int = 5, short_page: int | None = None, pages: int = PARIS_PAGES) -> dict[str, str]:
    available = {}
    counter = 0
    for number in range(1, pages + 1):
        if number == 1:
            cards = list(NAMED) + [paris(index) for index in range(len(NAMED), CARDS_PER_PAGE)]
            counter = CARDS_PER_PAGE
        else:
            size = CARDS_PER_PAGE
            if short_page == number:
                size = 3
            cards = [paris(counter + index) for index in range(size)]
            counter += size
        available[f"{LISTING}?page={number}"] = page(cards)
    available[f"{LISTING}?page={pages + 1}"] = page(
        [paris(counter + index) for index in range(last_page_cards)]
    )
    available[f"{LISTING}?page={pages + 2}"] = page([])
    return available


def fetch_from(available: dict[str, str]):
    def fetch(url: str, headers=()) -> str:
        if url not in available:
            raise ValueError(f"unexpected fetch: {url}")
        return available[url]

    return fetch


def test_address_block_splits_into_street_city_and_postcode():
    assert parse_address("80 Rue de Charonne\n\nParis\n(75011)") == ("80 Rue de Charonne", "Paris", "75011")
    assert parse_address("Rue des Poissonniers 13\n\nBruxelles\n(1000)")[2] == "1000"
    assert parse_address("") == ("", "", "")


def test_page_reads_every_card():
    cards = parse_page(page(NAMED))

    assert [entry["name"] for entry in cards] == ["Septime", "Le Chateaubriand", "Drip Cà Phê", "Belle Maria"]
    assert cards[0]["slug"] == "septime"
    assert cards[0]["label"] == "Gastro"
    assert cards[0]["postcode"] == "75011"


def test_crawl_keeps_paris_addresses_at_level_one():
    rows = crawl_le_fooding(fetch_from(site()), "2026-09-16")

    by_name = {row["name"]: row for row in rows}
    assert "Septime" in by_name and "Le Chateaubriand" in by_name
    assert "Drip Cà Phê" not in by_name and "Belle Maria" not in by_name
    septime = by_name["Septime"]
    assert septime["guide"] == "le_fooding"
    assert septime["level"] == 1
    assert (septime["locality"], septime["province"]) == ("Paris", "Île-de-France")
    assert septime["url"] == "https://lefooding.com/restaurants/septime"
    assert septime["postal_code"] == "75011"
    assert septime["address"] == "80 Rue de Charonne"
    assert septime["guide_category"] == "Gastro"
    assert {row["level"] for row in rows} == {1}


def test_crawl_stops_at_the_first_empty_page():
    rows = crawl_le_fooding(fetch_from(site()), "2026-09-16")

    assert len(rows) == PARIS_PAGES * CARDS_PER_PAGE + 5 - 2  # two non-Paris cards dropped


def test_a_short_page_before_the_last_is_refused():
    with pytest.raises(LeFoodingListingError, match="page 4 was short"):
        crawl_le_fooding(fetch_from(site(short_page=4)), "2026-09-16")


def test_a_listing_below_the_published_size_is_refused():
    with pytest.raises(LeFoodingListingError, match="below 500"):
        crawl_le_fooding(fetch_from(site(pages=3)), "2026-09-16")


def test_a_page_that_is_not_the_listing_is_refused():
    available = site()
    available[f"{LISTING}?page=1"] = "<html>Access denied</html>"

    with pytest.raises(LeFoodingListingError, match="below 500"):
        crawl_le_fooding(fetch_from(available), "2026-09-16")


def test_a_card_without_a_name_or_link_is_refused():
    with pytest.raises(LeFoodingListingError, match="no name or link"):
        parse_page(page([NAMED[0].replace('<h2 class="cardGuide__title e-uppercase">Septime</h2>', "")]))


def test_a_repeated_restaurant_is_refused():
    available = site()
    available[f"{LISTING}?page=2"] = page([NAMED[0]] + [paris(index) for index in range(100, 115)])

    with pytest.raises(LeFoodingListingError, match="duplicate restaurant URL"):
        crawl_le_fooding(fetch_from(available), "2026-09-16")
