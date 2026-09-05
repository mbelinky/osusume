from osusume.cli import _parser, _raw_input


def test_anchor_scope_cli_flags() -> None:
    args = _parser().parse_args(
        [
            "find",
            "cocktail bar near Anchor Bistro",
            "--near-place",
            "Anchor Bistro, Barcelona",
            "--max-min",
            "8",
            "--mode",
            "walk",
        ]
    )

    assert _raw_input(args)["scope"] == {
        "kind": "anchor",
        "place": "Anchor Bistro, Barcelona",
        "mode": "walk",
        "max_min": 8.0,
    }


def test_anchor_scope_cli_defaults() -> None:
    args = _parser().parse_args(["find", "bar near Anchor Bistro", "--near-place", "anchor-bistro"])

    assert _raw_input(args)["scope"] == {
        "kind": "anchor",
        "place": "anchor-bistro",
        "mode": "walk",
        "max_min": 10.0,
    }


def test_city_cli_flag_lands_in_every_scope_kind() -> None:
    cases = (
        (["--near", "41.39,2.17"], "near"),
        (["--route", "Girona", "Barcelona"], "route"),
        (["--near-place", "Plaça de Catalunya"], "anchor"),
    )

    for scope_args, kind in cases:
        args = _parser().parse_args(["find", "hotel", *scope_args, "--city", "Barcelona"])
        assert _raw_input(args)["scope"]["kind"] == kind
        assert _raw_input(args)["scope"]["city"] == "Barcelona"


def test_hotel_stay_cli_flags() -> None:
    args = _parser().parse_args([
        "find", "hotel", "--near-place", "Plaça de Catalunya", "--check-in", "2026-10-01",
        "--check-out", "2026-10-03",
    ])

    assert _raw_input(args)["stay"] == {"check_in": "2026-10-01", "check_out": "2026-10-03", "adults": 2}


def test_explicit_adults_can_override_dates_parsed_from_the_ask() -> None:
    args = _parser().parse_args(["find", "hotel for three nights", "--near-place", "Anchor", "--adults", "4"])

    assert _raw_input(args)["stay"] == {"adults": 4}
