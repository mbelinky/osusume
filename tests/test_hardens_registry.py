"""Offline cover for the Harden's Top 100 UK Restaurants crawl."""
import pytest

from osusume.hardens_registry import (
    LISTING,
    HardensListingError,
    crawl_hardens,
    level_for_rank,
    parse_ranking,
    town_name,
)


def article(rank: int, name: str, town: str, prefix: str, slug: str) -> str:
    return (
        f'<article class="gallery-hover" attrib-link="https://www.hardens.com/az/restaurants/'
        f'{town}/{prefix}/{slug}.htm">'
        '<img class="article-img" src="https://www.hardens.com/x.jpg" alt=" " />'
        f'<h1 class="article-number">{rank}</h1>'
        f'<h1 class="article-title">{name}</h1>'
        "</article>"
    )


NAMED = {
    4: ("The Ledbury", "london", "w11", "the-ledbury"),
    25: ("Core by Clare Smyth", "london", "w11", "core-by-clare-smyth"),
    60: ("The Kitchin", "edinburgh", "eh6", "the-kitchin"),
    99: ("Newcastle House", "newcastle-upon-tyne", "ne1", "newcastle-house"),
}


def listing(*, drop: tuple[int, ...] = (), duplicate_rank: int | None = None) -> str:
    articles = []
    for rank in range(1, 101):
        if rank in drop:
            continue
        name, town, prefix, slug = NAMED.get(rank, (f"Place {rank}", "bray", "sl6", f"place-{rank}"))
        shown = duplicate_rank if duplicate_rank is not None and rank == 100 else rank
        articles.append(article(shown, name, town, prefix, slug))
    return '<section class="cards">' + "".join(articles) + "</section>"


def fetch_from(page: str):
    def fetch(url: str, headers=()) -> str:
        assert url == LISTING
        return page

    return fetch


def test_rank_maps_to_guide_level():
    assert [level_for_rank(rank) for rank in (1, 20, 21, 50, 51, 100)] == [3, 3, 2, 2, 1, 1]
    with pytest.raises(HardensListingError):
        level_for_rank(101)


def test_town_slug_becomes_a_readable_place_name():
    assert town_name("newcastle-upon-tyne") == "Newcastle upon Tyne"
    assert town_name("east-grinstead") == "East Grinstead"
    assert town_name("bray") == "Bray"


def test_crawl_reads_the_whole_ranking():
    rows = crawl_hardens(fetch_from(listing()), "2026-09-16")

    assert len(rows) == 100
    assert [row["rank"] for row in rows] == list(range(1, 101))
    assert {row["guide"] for row in rows} == {"hardens"}
    assert [sum(1 for row in rows if row["level"] == level) for level in (3, 2, 1)] == [20, 30, 50]
    assert {row["verified_at"] for row in rows} == {"2026-09-16"}


def test_london_postcodes_become_greater_london():
    rows = {row["name"]: row for row in crawl_hardens(fetch_from(listing()), "2026-09-16")}

    ledbury = rows["The Ledbury"]
    assert (ledbury["locality"], ledbury["province"]) == ("London", "Greater London")
    assert ledbury["level"] == 3
    assert ledbury["url"] == "https://www.hardens.com/az/restaurants/london/w11/the-ledbury.htm"
    assert rows["Core by Clare Smyth"]["province"] == "Greater London"


def test_other_towns_keep_their_own_name_as_province():
    rows = {row["name"]: row for row in crawl_hardens(fetch_from(listing()), "2026-09-16")}

    assert (rows["The Kitchin"]["locality"], rows["The Kitchin"]["province"]) == ("Edinburgh", "Edinburgh")
    assert rows["Newcastle House"]["locality"] == "Newcastle upon Tyne"


def test_short_ranking_is_refused():
    with pytest.raises(HardensListingError, match="parsed 98 of 100"):
        parse_ranking(listing(drop=(7, 8)), "2026-09-16")


def test_page_without_articles_is_refused():
    with pytest.raises(HardensListingError, match="parsed 0 of 100"):
        parse_ranking("<html>Access denied</html>", "2026-09-16")


def test_ranking_that_does_not_run_one_to_hundred_is_refused():
    with pytest.raises(HardensListingError, match="does not run 1-100"):
        parse_ranking(listing(duplicate_rank=99), "2026-09-16")


def test_article_without_a_link_is_refused():
    page = listing().replace(
        '<article class="gallery-hover" attrib-link="https://www.hardens.com/az/restaurants/bray/sl6/place-1.htm">',
        '<article class="gallery-hover">',
        1,
    )

    with pytest.raises(HardensListingError, match="no link"):
        parse_ranking(page, "2026-09-16")


def test_link_without_a_town_and_postcode_is_refused():
    page = listing().replace("/az/restaurants/bray/sl6/place-1.htm", "/az/restaurants/place-1.htm", 1)

    with pytest.raises(HardensListingError, match="no town and postcode"):
        parse_ranking(page, "2026-09-16")
