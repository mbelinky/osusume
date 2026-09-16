"""Offline fixtures for the Michelin Bib Gourmand listing and detail pages."""
import pytest

from osusume.michelin_registry import crawl_michelin_bib, listing_url, parse_listing

GB_BIB = listing_url("GB", "bib")
FR_BIB = listing_url("FR", "bib")


def gb_listing(total=1):
    return f'''United Kingdom : 1-1 of {total} restaurants
    <div class="card__menu js-restaurant__list_item" data-id="1241126" data-lat="52.954166" data-lng="-1.1526441">
    <div data-restaurant-country="gb" data-dtm-distinction="bib" data-restaurant-name="Piccalilli"
      data-dtm-city="Nottingham" data-dtm-region="Nottingham"></div>
    <a href="/en/nottingham/nottingham/restaurant/piccalilli">Piccalilli</a></div>'''


def gb_star_listing():
    return '''United Kingdom : 1-1 of 1 restaurants
    <div class="card__menu js-restaurant__list_item" data-id="321" data-lat="51.5126" data-lng="-0.203">
    <div data-restaurant-country="gb" data-dtm-distinction="1 star" data-restaurant-name="Trivet"
      data-dtm-city="London" data-dtm-region="Greater London"></div>
    <a href="/en/greater-london/london/restaurant/trivet">Trivet</a></div>'''


def gb_detail(award="Bib Gourmand: good quality, good value cooking", country="GBR"):
    return f'''<script type="application/ld+json">{{"@type":"Restaurant", "name":"Piccalilli",
    "address":{{"postalCode":"NG1 3AL", "streetAddress":"5 Broad Street",
    "addressCountry":"{country}", "addressRegion":"Nottingham"}},
    "award":{{"@type":"Award", "awardFor":"{award}"}}}}</script>'''


def fr_listing():
    return '''Ile-de-France : 1-1 of 1 restaurants
    <div class="card__menu js-restaurant__list_item" data-id="1241790" data-lat="48.8637" data-lng="2.3476">
    <div data-restaurant-country="fr" data-dtm-distinction="bib" data-restaurant-name="Baffo"
      data-dtm-city="Paris" data-dtm-region="Ile-de-France"></div>
    <a href="/en/ile-de-france/paris/restaurant/baffo">Baffo</a></div>
    <div class="card__menu js-restaurant__list_item" data-id="1241791" data-lat="45.76" data-lng="4.83">
    <div data-restaurant-country="fr" data-dtm-distinction="bib" data-restaurant-name="Elsewhere"
      data-dtm-city="Lyon" data-dtm-region="Auvergne-Rhone-Alpes"></div>
    <a href="/en/auvergne-rhone-alpes/lyon/restaurant/elsewhere">Elsewhere</a></div>'''


def fr_detail():
    return '''<script type="application/ld+json">{"@type":"Restaurant", "name":"Baffo",
    "address":{"postalCode":"75004", "streetAddress":"12 rue Pecquay",
    "addressCountry":"FRA", "addressRegion":"Ile-de-France"},
    "award":{"@type":"Award", "awardFor":"Bib Gourmand: good quality, good value cooking"}}</script>'''


def test_bib_rows_are_level_one_entries_of_their_own_guide():
    rows = crawl_michelin_bib(lambda url: gb_listing() if url == GB_BIB else gb_detail(), "2026-09-16", "GB")

    assert [(row["name"], row["locality"], row["province"], row["level"], row["guide"]) for row in rows] == [
        ("Piccalilli", "Nottingham", "Nottingham", 1, "michelin_bib")
    ]
    assert rows[0]["postal_code"] == "NG1 3AL"
    assert rows[0]["url"].startswith("https://guide.michelin.com/")


def test_bib_detail_must_carry_the_bib_award():
    page = gb_detail(award="One Star: High quality cooking")
    with pytest.raises(ValueError, match="award mismatch"):
        crawl_michelin_bib(lambda url: gb_listing() if url == GB_BIB else page, "2026-09-16", "GB")


def test_bib_refuses_a_detail_page_from_another_country():
    page = gb_detail(country="DEU")
    with pytest.raises(ValueError, match="outside GB"):
        crawl_michelin_bib(lambda url: gb_listing() if url == GB_BIB else page, "2026-09-16", "GB")


def test_bib_pagination_count_must_match():
    with pytest.raises(ValueError, match="pagination incomplete"):
        crawl_michelin_bib(lambda url: gb_listing(2), "2026-09-16", "GB")


def test_bib_refuses_a_changed_page_shape():
    with pytest.raises(ValueError, match="no Bib Gourmand"):
        parse_listing("<html>Access denied</html>", "GB", "bib")


def test_each_distinction_reads_only_its_own_rows():
    with pytest.raises(ValueError, match="no Bib Gourmand"):
        parse_listing(gb_star_listing(), "GB", "bib")
    with pytest.raises(ValueError, match="no starred"):
        parse_listing(gb_listing(), "GB")


def test_bib_france_keeps_only_the_configured_region():
    rows = crawl_michelin_bib(lambda url: fr_listing() if url == FR_BIB else fr_detail(), "2026-09-16", "FR")

    assert [(row["name"], row["locality"], row["province"]) for row in rows] == [
        ("Baffo", "Paris", "Ile-de-France")
    ]


def test_bib_listing_paths_are_the_published_distinction_filters():
    assert GB_BIB == "https://guide.michelin.com/en/gb/restaurants/bib-gourmand"
    assert FR_BIB == "https://guide.michelin.com/en/fr/ile-de-france/restaurants/bib-gourmand"
