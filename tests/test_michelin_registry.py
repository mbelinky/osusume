import pytest

from osusume.michelin_registry import BASE, COUNTRIES, LISTING, crawl_michelin, parse_listing


def listing(total=1):
    return f'''Spain : 1-1 of {total} restaurants
    <div class="card__menu js-restaurant__list_item" data-id="123" data-lat="41.4" data-lng="2.1">
    <div data-restaurant-country="es" data-dtm-distinction="3 star"
      data-restaurant-name="ABaC" data-dtm-city="Barcelona" data-dtm-region="Catalonia"></div>
    <a href="/en/catalunya/barcelona/restaurant/abac">ABaC</a></div>
    <div class="js-restaurant__list_item js-restaurants__empty_new_restaurants" data-id="999" data-lat="0" data-lng="0">
    <div data-restaurant-country="es" data-dtm-distinction="1 star" data-restaurant-name="Ad"
    data-dtm-city="Madrid" data-dtm-region="Madrid"></div>
    <a href="/en/madrid/restaurant/ad">Ad</a></div>'''


def detail(stars="Three Stars", postcode="08022"):
    return f'''<script type="application/ld+json">{{"@type":"Restaurant", "name":"ABaC",
    "address":{{"postalCode":"{postcode}", "streetAddress":"Avenida del Tibidabo 1"}},
    "starRating":"{stars}: Exceptional cuisine"}}</script>'''


def test_michelin_crawl_uses_star_rows_and_postcode_province():
    rows = crawl_michelin(lambda url: listing() if url == LISTING else detail(), "2026-09-16")
    assert len(rows) == 1
    assert rows[0]["name"] == "ABaC"
    assert rows[0]["province"] == "Barcelona"
    assert rows[0]["level"] == 3
    assert rows[0]["verified_at"] == "2026-09-16"


def test_michelin_pagination_count_must_match():
    with pytest.raises(ValueError, match="pagination incomplete"):
        crawl_michelin(lambda url: listing(2), "2026-09-16")


@pytest.mark.parametrize("page", [detail("One Star"), detail(postcode="")])
def test_michelin_refuses_missing_province_or_conflicting_award(page):
    with pytest.raises(ValueError):
        crawl_michelin(lambda url: listing() if url == LISTING else page, "2026-09-16")


def test_michelin_refuses_changed_page_shape():
    with pytest.raises(ValueError, match="no starred"):
        parse_listing("<html>Access denied</html>")


def gb_listing(total=1):
    return f'''United Kingdom : 1-1 of {total} restaurants
    <div class="card__menu js-restaurant__list_item" data-id="321" data-lat="51.5126" data-lng="-0.203">
    <div data-restaurant-country="gb" data-dtm-distinction="3 star"
      data-restaurant-name="CORE by Clare Smyth" data-dtm-city="London" data-dtm-region="Greater London"></div>
    <a href="/en/greater-london/london/restaurant/core-by-clare-smyth">CORE by Clare Smyth</a></div>'''


def gb_detail(country="GBR"):
    return f'''<script type="application/ld+json">{{"@type":"Restaurant", "name":"CORE by Clare Smyth",
    "address":{{"postalCode":"W11 2PN", "streetAddress":"92 Kensington Park Road",
    "addressCountry":"{country}", "addressRegion":"Greater London"}},
    "starRating":"Three Stars: Exceptional cuisine"}}</script>'''


def fr_listing():
    return '''Ile-de-France : 1-1 of 1 restaurants
    <div class="card__menu js-restaurant__list_item" data-id="101" data-lat="48.8566" data-lng="2.3141">
    <div data-restaurant-country="fr" data-dtm-distinction="3 star" data-restaurant-name="Le Cinq"
      data-dtm-city="Paris" data-dtm-region="Ile-de-France"></div>
    <a href="/en/ile-de-france/paris/restaurant/le-cinq">Le Cinq</a></div>
    <div class="card__menu js-restaurant__list_item" data-id="102" data-lat="45.76" data-lng="4.83">
    <div data-restaurant-country="fr" data-dtm-distinction="1 star" data-restaurant-name="Elsewhere"
      data-dtm-city="Lyon" data-dtm-region="Auvergne-Rhone-Alpes"></div>
    <a href="/en/auvergne-rhone-alpes/lyon/restaurant/elsewhere">Elsewhere</a></div>'''


def fr_detail():
    return '''<script type="application/ld+json">{"@type":"Restaurant", "name":"Le Cinq",
    "address":{"postalCode":"75008", "streetAddress":"31 avenue George V",
    "addressCountry":"FRA", "addressRegion":"Ile-de-France"},
    "starRating":"Three Stars: Exceptional cuisine"}</script>'''


def test_michelin_gb_takes_locality_and_province_from_the_listing():
    listing_url = BASE + COUNTRIES["GB"]["path"]
    rows = crawl_michelin(lambda url: gb_listing() if url == listing_url else gb_detail(), "2026-09-16", "GB")
    assert [(row["name"], row["locality"], row["province"], row["level"]) for row in rows] == [
        ("CORE by Clare Smyth", "London", "Greater London", 3)
    ]
    assert rows[0]["postal_code"] == "W11 2PN"


def test_michelin_gb_refuses_a_detail_page_from_another_country():
    listing_url = BASE + COUNTRIES["GB"]["path"]
    with pytest.raises(ValueError, match="outside GB"):
        crawl_michelin(lambda url: gb_listing() if url == listing_url else gb_detail("DEU"), "2026-09-16", "GB")


def test_michelin_fr_keeps_only_the_configured_region():
    listing_url = BASE + COUNTRIES["FR"]["path"]
    rows = crawl_michelin(lambda url: fr_listing() if url == listing_url else fr_detail(), "2026-09-16", "FR")
    assert [(row["name"], row["locality"], row["province"]) for row in rows] == [
        ("Le Cinq", "Paris", "Ile-de-France")
    ]


def test_michelin_country_listing_total_must_name_its_scope():
    with pytest.raises(ValueError, match="United Kingdom"):
        parse_listing(fr_listing(), "GB")
