import pytest

from osusume.fifty_best_registry import (
    LIST_URL,
    SITEMAP_URL,
    FiftyBestListError,
    crawl_fifty_best,
    level_for_rank,
    parse_list,
)


def item(rank: int, name: str, city: str, linked: bool = True) -> str:
    link = f'<a href="/restaurants/best-in-the-world/the-list/{name.casefold().replace(" ", "-")}.html">' if linked else ""
    close = "</a>" if linked else ""
    return (
        '<div class="list-item" ><div class="list-item-contents" >'
        f'<div class="item-top" ><p class="rank " >{rank}</p></div>'
        f'<div class="item-bottom" >{link}<h2>{name}</h2>{close}<p>{city}</p></div>'
        "</div></div>"
    )


def list_page(*, missing_rank: int | None = None) -> str:
    top = [item(rank, f"Top {rank}", "Lima") for rank in range(1, 51)]
    rest = [item(rank, f"Rest {rank}", "Lima", linked=False) for rank in range(51, 101)]
    top[14] = item(15, "Ikoyi", "London")
    top[44] = item(45, "Arpège", "Paris")
    rest[27] = item(78, "Cocina Hermanos Torres", "Barcelona", linked=False)
    if missing_rank is not None:
        top = [row for row in top if f'>{missing_rank}</p>' not in row]
    return (
        '<div data-list="1-50" class="list-grid visible" >' + "".join(top) + "</div>"
        '<div data-list="51-100" class="list-grid hidden" >' + "".join(rest) + "</div>"
    )


def sitemap_page(*, omit: tuple[str, ...] = ()) -> str:
    listed = [("Peru", "Lima", f"Top-{rank}") for rank in range(1, 51)]
    listed += [("Peru", "Lima", f"Rest-{rank}") for rank in range(51, 101)]
    listed += [
        ("UK", "London", "Ikoyi"),
        ("France", "Paris", "Arpege"),
        ("Spain", "Barcelona", "Cocina-Hermanos-Torres"),
    ]
    locations = [
        f"https://www.the50.com/discovery/Establishments/{country}/{city}/{name}.html"
        for country, city, name in listed
        if name not in omit
    ]
    return "<urlset>" + "".join(f"<loc>{location}</loc>" for location in locations) + "</urlset>"


def pages(*, omit: tuple[str, ...] = (), **extra: str) -> dict[str, str]:
    return {LIST_URL: list_page(), SITEMAP_URL: sitemap_page(omit=omit), **extra}


def fetch_from(available: dict[str, str]):
    def fetch(url: str) -> str:
        if url not in available:
            raise ValueError(f"unexpected fetch: {url}")
        return available[url]

    return fetch


def test_rank_maps_to_guide_level():
    assert [level_for_rank(rank) for rank in (1, 10, 11, 50, 51, 100)] == [3, 3, 2, 2, 1, 1]
    with pytest.raises(FiftyBestListError):
        level_for_rank(101)


def test_list_page_must_rank_one_to_one_hundred():
    with pytest.raises(FiftyBestListError, match="1-50"):
        parse_list(list_page(missing_rank=15))


def test_list_page_without_blocks_is_refused():
    with pytest.raises(FiftyBestListError, match="no 1-50 block"):
        parse_list("<html>Access denied</html>")


def test_country_comes_from_the_official_establishment_directory():
    rows = crawl_fifty_best(fetch_from(pages()), "2026-09-16", "GB")

    assert [(row["name"], row["locality"], row["level"], row["rank"]) for row in rows] == [("Ikoyi", "London", 2, 15)]
    assert rows[0]["guide"] == "fifty_best"
    assert rows[0]["url"] == "https://www.theworlds50best.com/restaurants/best-in-the-world/the-list/ikoyi.html"


def test_unlinked_entry_keeps_the_directory_page_as_its_url():
    rows = crawl_fifty_best(fetch_from(pages()), "2026-09-16", "ES")

    assert [(row["name"], row["level"]) for row in rows] == [("Cocina Hermanos Torres", 1)]
    assert rows[0]["url"].endswith("/discovery/Establishments/Spain/Barcelona/Cocina-Hermanos-Torres.html")


def test_profile_page_settles_a_country_the_directory_does_not_give():
    profile = (
        '<script type="application/ld+json">{"@context":"https://schema.org","@type":"Restaurant",'
        '"name":"Arpège","address":{"@type":"PostalAddress",'
        '"streetAddress":"84 rue de Varenne, 75007 Paris, France","addressLocality":"Paris"}}</script>'
    )
    available = pages(
        omit=("Arpege",),
        **{"https://www.theworlds50best.com/restaurants/best-in-the-world/the-list/arpège.html": profile},
    )

    rows = crawl_fifty_best(fetch_from(available), "2026-09-16", "FR")

    assert [(row["name"], row["locality"], row["province"], row["level"]) for row in rows] == [
        ("Arpège", "Paris", "Paris", 2)
    ]


def test_entries_without_an_official_country_page_are_reported_not_invented():
    available = pages(omit=("Cocina-Hermanos-Torres",))
    unresolved: list[str] = []

    rows = crawl_fifty_best(fetch_from(available), "2026-09-16", "ES", unresolved)

    assert rows == []
    assert any("Cocina Hermanos Torres" in note for note in unresolved)
