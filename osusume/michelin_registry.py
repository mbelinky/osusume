"""Read an official Michelin distinction listing and restaurant JSON-LD."""
from __future__ import annotations

import json
import re
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

BASE = "https://guide.michelin.com"
STAR_FILTER = "restaurants/3-stars-michelin/2-stars-michelin/1-star-michelin"
BIB_FILTER = "restaurants/bib-gourmand"


def _star_listing_level(distinction):
    level = re.fullmatch(r"([123]) star", distinction)
    return int(level[1]) if level else None


def _star_detail_level(detail):
    stars = {"One Star": 1, "Two Stars": 2, "Three Stars": 3}
    rating = str(detail.get("starRating", ""))
    return next((level for text, level in stars.items() if rating.startswith(text)), None)


def _bib_listing_level(distinction):
    return 1 if distinction == "bib" else None


def _bib_detail_level(detail):
    award = detail.get("award")
    award_for = award.get("awardFor") if isinstance(award, dict) else award
    return 1 if str(award_for or "").startswith("Bib Gourmand") else None


# One entry per distinction the crawler reads: its listing filter, the guide the
# rows are stored under, and the two award readings that must agree. Bib Gourmand
# is its own guide at level 1 because it is a separate distinction, not a lesser
# star, and Stage 2 orders injections by card weight and level.
DISTINCTIONS = {
    "stars": {"filter": STAR_FILTER, "guide": "michelin", "label": "starred",
              "listing_level": _star_listing_level, "detail_level": _star_detail_level},
    "bib": {"filter": BIB_FILTER, "guide": "michelin_bib", "label": "Bib Gourmand",
            "listing_level": _bib_listing_level, "detail_level": _bib_detail_level},
}
# Every country the crawler reads, with the scope label its listing counts under.
# ``region`` restricts a country listing to one guide region; ``postcode_province``
# keeps Spain's postcode rule while other countries report their own region.
COUNTRIES = {
    "ES": {"code": "es", "label": "Spain", "prefix": "/en/es/", "region": None,
           "address_country": None, "postcode_province": True},
    "GB": {"code": "gb", "label": "United Kingdom", "prefix": "/en/gb/", "region": None,
           "address_country": "GBR", "postcode_province": False},
    "FR": {"code": "fr", "label": "Ile-de-France", "prefix": "/en/fr/ile-de-france/",
           "region": "Ile-de-France", "address_country": "FRA", "postcode_province": False},
}
for _scope in COUNTRIES.values():
    # The starred listing every caller of ``path`` means.
    _scope["path"] = _scope["prefix"] + STAR_FILTER
LISTING = BASE + COUNTRIES["ES"]["path"]
PROVINCES = dict(enumerate([
    "Álava", "Albacete", "Alicante", "Almería", "Ávila", "Badajoz", "Illes Balears", "Barcelona",
    "Burgos", "Cáceres", "Cádiz", "Castellón", "Ciudad Real", "Córdoba", "A Coruña", "Cuenca",
    "Girona", "Granada", "Guadalajara", "Gipuzkoa", "Huelva", "Huesca", "Jaén", "León", "Lleida",
    "La Rioja", "Lugo", "Madrid", "Málaga", "Murcia", "Navarra", "Ourense", "Asturias", "Palencia",
    "Las Palmas", "Pontevedra", "Salamanca", "Santa Cruz de Tenerife", "Cantabria", "Segovia",
    "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia", "Valladolid", "Bizkaia",
    "Zamora", "Zaragoza", "Ceuta", "Melilla",
], 1))


def listing_url(country="ES", distinction="stars"):
    return BASE + COUNTRIES[country]["prefix"] + DISTINCTIONS[distinction]["filter"]


class ListingParser(HTMLParser):
    def __init__(self, code="es", distinction="stars"):
        super().__init__()
        self.code = code
        self.level_of = DISTINCTIONS[distinction]["listing_level"]
        self.rows = []
        self.current = None
        self.pages = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if "js-restaurant__list_item" in classes:
            self.current = None
            if attrs.get("data-id", "").isdigit() and "js-restaurants__empty_new_restaurants" not in classes:
                self.current = {
                    "latitude": float(attrs["data-lat"]), "longitude": float(attrs["data-lng"]),
                }
                self.rows.append(self.current)
        if self.current is not None and attrs.get("data-restaurant-country") == self.code:
            level = self.level_of(attrs.get("data-dtm-distinction", ""))
            if level:
                self.current.update(name=attrs["data-restaurant-name"], locality=attrs["data-dtm-city"],
                                    region=attrs["data-dtm-region"], level=level)
        href = attrs.get("href", "")
        if tag == "a" and "/restaurant/" in href and self.current is not None and "{{" not in href:
            self.current.setdefault("url", urljoin(BASE, href))
        if tag == "a" and "offset" in attrs and re.search(r"/page/\d+$", href):
            self.pages.add(urljoin(BASE, href))


def parse_listing(html, country="ES", distinction="stars"):
    scope = COUNTRIES[country]
    spec = DISTINCTIONS[distinction]
    parser = ListingParser(scope["code"], distinction)
    parser.feed(html)
    rows = [row for row in parser.rows if "name" in row and "url" in row]
    if scope["region"]:
        rows = [row for row in rows if row.get("region") == scope["region"]]
    total = re.search(
        re.escape(scope["label"]) + r"\s*:\s*[\d,]+-[\d,]+\s+of\s+([\d,]+)\s+restaurants", html
    )
    if not rows or not total:
        raise ValueError(
            f"Michelin listing has no {spec['label']} {scope['label']} rows or total; refusing refresh"
        )
    return rows, parser.pages, int(total[1].replace(",", ""))


def parse_detail(html):
    for raw in re.findall(r'<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S):
        data = json.loads(unescape(raw))
        for item in data if isinstance(data, list) else [data]:
            if item.get("@type") == "Restaurant":
                return item
    raise ValueError("Michelin detail has no Restaurant JSON-LD; refusing refresh")


def crawl_michelin(fetch, verified_at, country="ES", distinction="stars"):
    scope = COUNTRIES[country]
    spec = DISTINCTIONS[distinction]
    pending = [listing_url(country, distinction)]
    visited = set()
    entries = {}
    expected = None
    while pending:
        url = pending.pop(0)
        if url in visited:
            continue
        visited.add(url)
        rows, pages, total = parse_listing(fetch(url), country, distinction)
        if expected is not None and expected != total:
            raise ValueError("Michelin total changed during pagination; retry refresh")
        expected = total
        for row in rows:
            entries[row["url"]] = row
        pending.extend(sorted(pages - visited))
    if len(entries) != expected:
        raise ValueError(f"Michelin pagination incomplete: {len(entries)} of {expected}")
    for row in entries.values():
        detail_urls = [row["url"], *(
            row["url"].replace(BASE + "/en/", BASE + f"/{locale}/en/", 1)
            for locale in ("gb", "us")
        )]
        for detail_url in detail_urls:
            try:
                detail_page = fetch(detail_url)
                if detail_url != row["url"]:
                    row["detail_url"] = detail_url
                break
            except (ValueError, OSError):
                # Some global detail URLs return an empty response while an
                # official English country edition serves the same restaurant.
                if detail_url == detail_urls[-1]:
                    raise
        detail = parse_detail(detail_page)
        address = detail.get("address", {})
        postcode = str(address.get("postalCode", ""))
        if scope["postcode_province"]:
            if not re.fullmatch(r"\d{5}", postcode) or int(postcode[:2]) not in PROVINCES:
                raise ValueError(f"Missing Spain province/postcode for {row['name']}")
            province = PROVINCES[int(postcode[:2])]
        else:
            if str(address.get("addressCountry", "")) != scope["address_country"]:
                raise ValueError(f"Michelin detail is outside {country} for {row['name']}")
            province = str(row.get("region") or "")
            if not province:
                raise ValueError(f"Missing Michelin region for {row['name']}")
        if spec["detail_level"](detail) != row["level"]:
            raise ValueError(f"Michelin listing/detail award mismatch for {row['name']}")
        row.update(guide=spec["guide"], province=province, verified_at=verified_at,
                   address=address.get("streetAddress", ""), postal_code=postcode)
    return list(entries.values())


def crawl_michelin_bib(fetch, verified_at, country="GB"):
    """The Bib Gourmand listing of the same country scope, every row at level 1."""
    return crawl_michelin(fetch, verified_at, country, "bib")
