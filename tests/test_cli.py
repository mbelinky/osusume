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
