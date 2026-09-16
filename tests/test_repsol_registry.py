from __future__ import annotations

import pytest

from osusume.repsol_registry import LISTING_URL, RepsolListingError, crawl_repsol


def listing(*, one_count: int = 2, one_cards: str | None = None) -> str:
    if one_cards is None:
        one_cards = "".join(
            (
                card(
                    "Sato i Tanaka",
                    "/es/fichas/restaurante/sato-i-tanaka-321874/",
                    "Barcelona",
                    "Barcelona",
                ),
                card(
                    "Suto",
                    "/es/fichas/restaurante/suto-332627/",
                    "Barcelona",
                    "Barcelona",
                ),
            )
        )
    return f"""
    <div class="container-gene-result" data-category="3">
      <span class="result-count-num">1</span>
      {card("Disfrutar", "/es/fichas/restaurante/disfrutar-1/", "Barcelona", "Barcelona")}
    </div>
    <div class="container-gene-result" data-category="2">
      <span class="result-count-num">1</span>
      {card("Les Cols", "/es/fichas/restaurante/les-cols-2/", "Olot", "Girona")}
    </div>
    <div class="container-gene-result" data-category="1">
      <span class="result-count-num">{one_count}</span>
      {one_cards}
    </div>
    <div class="container-gene-result" data-category="R">
      <span class="result-count-num">2</span>
      {card("Sensato", "/es/fichas/restaurante/sensato-3/", "Barcelona", "Barcelona")}
      {card("Os-Kuro", "/es/fichas/restaurante/os-kuro-4/", "Barcelona", "Barcelona")}
    </div>
    """


def card(name: str, href: str, locality: str, province: str) -> str:
    return f"""
    <li data-provincia="{province}" data-localidad="{locality}">
      <a href="{href}" title="{name}" class="galardonado-card">
        <span class="name">{name}</span>
      </a>
    </li>
    """


def test_crawl_returns_only_soles_with_registry_schema() -> None:
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return listing()

    rows = crawl_repsol(fetch, "2026-09-16")

    assert calls == [LISTING_URL]
    assert [row["name"] for row in rows] == [
        "Disfrutar",
        "Les Cols",
        "Sato i Tanaka",
        "Suto",
    ]
    assert [row["level"] for row in rows] == [3, 2, 1, 1]
    assert {row["name"] for row in rows}.isdisjoint({"Sensato", "Os-Kuro"})
    sato = next(row for row in rows if row["name"] == "Sato i Tanaka")
    assert sato == {
        "name": "Sato i Tanaka",
        "locality": "Barcelona",
        "province": "Barcelona",
        "guide": "repsol",
        "level": 1,
        "url": "https://www.guiarepsol.com/es/fichas/restaurante/sato-i-tanaka-321874/",
        "verified_at": "2026-09-16",
    }


def test_visible_name_is_used_when_anchor_has_no_title_and_entities_are_decoded() -> None:
    one = """
    <li data-provincia="A Coruña" data-localidad="Santiago de Compostela">
      <a href="https://www.guiarepsol.com/es/fichas/restaurante/a-1/"
         class="galardonado-card"><span class="name">Casa &amp; Mar</span></a>
    </li>
    """

    rows = crawl_repsol(
        lambda _url: listing(one_count=1, one_cards=one), "2026-01-02"
    )

    assert rows[-1]["name"] == "Casa & Mar"
    assert rows[-1]["province"] == "A Coruña"


def test_crawl_fails_closed_on_truncated_section() -> None:
    with pytest.raises(RepsolListingError, match="declares 3.*contains 2"):
        crawl_repsol(lambda _url: listing(one_count=3), "2026-09-16")


def test_crawl_fails_closed_when_a_sol_section_is_missing() -> None:
    page = listing().replace('data-category="2"', 'data-category="R"')

    with pytest.raises(RepsolListingError, match="missing Sol sections"):
        crawl_repsol(lambda _url: page, "2026-09-16")


@pytest.mark.parametrize("stamp", ["2026-9-16", "2026-09-16T00:00:00Z", "today"])
def test_verified_at_must_be_an_iso_date(stamp: str) -> None:
    with pytest.raises(ValueError, match="ISO date"):
        crawl_repsol(lambda _url: listing(), stamp)


def test_fetch_must_return_text() -> None:
    with pytest.raises(TypeError, match="return str"):
        crawl_repsol(lambda _url: b"html", "2026-09-16")  # type: ignore[return-value]
