from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .adapters import GoplacesAdapter, ModelAdapter, RecordedAdapters, ReplayStore, SnapshotRecorder, WebAdapter
from .cards import load_card, promote_card
from .config import load_config, public_config, set_config_value
from .funnel import Funnel


def _near(value: str) -> tuple[float, float]:
    try:
        lat, lng = value.split(",", 1)
        return float(lat), float(lng)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--near must be LAT,LNG") from exc


def _split_values(values: list[str] | None, separator: str = ";") -> list[str]:
    result = []
    for value in values or []:
        result.extend(item.strip() for item in value.split(separator) if item.strip())
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osusume", description="Evidence-gated place recommendations")
    commands = parser.add_subparsers(dest="command", required=True)

    find = commands.add_parser("find", help="find and verify recommendations")
    find.add_argument("ask", nargs="?")
    scope = find.add_mutually_exclusive_group()
    scope.add_argument("--near", type=_near, metavar="LAT,LNG")
    scope.add_argument("--route", nargs=2, metavar=("FROM", "TO"))
    scope.add_argument("--near-place", metavar="NAME_OR_PLACE_ID")
    find.add_argument("--radius-km", type=float, default=5.0)
    find.add_argument("--max-min", type=float, default=10.0)
    find.add_argument("--mode", choices=("walk", "drive", "bicycle", "transit"), default="walk")
    find.add_argument("--when")
    find.add_argument("--max-detour-min", type=float)
    find.add_argument("--prefs", action="append", default=[])
    find.add_argument("--exclude", action="append", default=[])
    find.add_argument("--card")
    find.add_argument("--depth", choices=("quick", "full"), default="full")
    find.add_argument("--contact-drafts", action="store_true")
    find.add_argument("--replay", type=Path)
    find.add_argument("--json", action="store_true", dest="as_json")
    find.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)

    card = commands.add_parser("card", help="inspect or promote category cards")
    card_commands = card.add_subparsers(dest="card_command", required=True)
    card_commands.add_parser("list")
    show = card_commands.add_parser("show")
    show.add_argument("name")
    promote = card_commands.add_parser("promote")
    promote.add_argument("name")

    config = commands.add_parser("config", help="inspect or change config")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    config_commands.add_parser("show")
    config_set = config_commands.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value")
    return parser


def _raw_input(args: argparse.Namespace) -> dict[str, Any]:
    scope = None
    if args.near:
        scope = {"kind": "near", "lat": args.near[0], "lng": args.near[1], "radius_km": args.radius_km}
    elif args.route:
        scope = {"kind": "route", "from": args.route[0], "to": args.route[1], "radius_km": args.radius_km}
    elif args.near_place:
        scope = {"kind": "anchor", "place": args.near_place, "mode": args.mode, "max_min": args.max_min}
    return {
        "ask": args.ask or "",
        "scope": scope,
        "when": args.when,
        "max_detour_min": args.max_detour_min,
        "prefs": _split_values(args.prefs),
        "exclude": _split_values(args.exclude, separator=","),
        "card": args.card,
        "depth": args.depth,
        "contact_drafts": args.contact_drafts,
    }


def _run_find(args: argparse.Namespace, config: dict[str, Any]) -> int:
    replay = ReplayStore(args.replay) if args.replay else None
    if replay:
        raw_input = replay.input
        adapters = RecordedAdapters(None, None, None, replay=replay)
        recorder = None
        run_at = replay.run_at
    else:
        if not args.ask:
            raise ValueError("find requires an ask unless --replay is used")
        if not args.near and not args.route and not args.near_place:
            raise ValueError("find requires --near, --route, or --near-place unless --replay is used")
        raw_input = _raw_input(args)
        run_dir = args.run_dir or config["paths"]["runs"] / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        recorder = SnapshotRecorder(run_dir)
        run_at = recorder.run_at
        adapters = RecordedAdapters(
            GoplacesAdapter(),
            WebAdapter(config["web"]["endpoint"]),
            ModelAdapter(config),
            recorder=recorder,
        )
    result = Funnel(config, adapters, now=run_at).run(raw_input)
    if recorder:
        recorder.finish(raw_input, result)
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["human"])
        if result["refusal"]:
            print("Widen options: " + "; ".join(result["widen_options"]))
    return 0


def _run_card(args: argparse.Namespace, config: dict[str, Any]) -> int:
    cards_dir = config["paths"]["cards"]
    if args.card_command == "list":
        for path in sorted(cards_dir.glob("*.yaml")):
            print(path.stem)
        return 0
    if args.card_command == "show":
        matches = [cards_dir / f"{args.name}.yaml", *sorted(cards_dir.glob(f"{args.name}_*.yaml")), config["paths"]["drafts"] / f"{args.name}.yaml"]
        for path in matches:
            if path.exists():
                card = load_card(path, config["freshness_days"])
                print(yaml.safe_dump(card, sort_keys=False, allow_unicode=True).rstrip())
                return 0
        raise FileNotFoundError(f"card not found: {args.name}")
    path = promote_card(args.name, cards_dir, config["paths"]["drafts"], config["freshness_days"])
    print(path)
    return 0


def _run_config(args: argparse.Namespace, config: dict[str, Any]) -> int:
    if args.config_command == "show":
        print(yaml.safe_dump(public_config(config), sort_keys=False, allow_unicode=True).rstrip())
        return 0
    set_config_value(args.key, args.value)
    print(f"set {args.key}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = _parser()
    args = parser.parse_args(argv)
    config = load_config()
    try:
        if args.command == "find":
            code = _run_find(args, config)
        elif args.command == "card":
            code = _run_card(args, config)
        else:
            code = _run_config(args, config)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        parser.exit(2, f"osusume: {exc}\n")
    raise SystemExit(code)
