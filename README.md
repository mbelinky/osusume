# Osusume

> **Super beta.** Osusume works end to end and its offline tests pass, but its commands, configuration, output, and evidence rules are still changing. Do not depend on it for unattended or safety-critical decisions.

[![Tests](https://github.com/mbelinky/osusume/actions/workflows/test.yml/badge.svg)](https://github.com/mbelinky/osusume/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-6b6257.svg)](LICENSE)

Osusume means “recommendation” in Japanese. It finds real-world places and events, then refuses claims that do not pass explicit source and freshness rules.

Most recommendation tools can produce a plausible answer from thin evidence. Osusume takes a narrower route: ordinary Python code controls discovery, freshness, distance, and claim gates. Model calls may parse the request, judge quoted evidence, and phrase supported results. They do not get to bypass the gates.

## What it does

- Searches near a point, along a driving route, or within a travel-time budget of a named place, through the `goplaces` command-line tool.
- Adds current web evidence through Exa when a category card requires it.
- Checks operational status, arrival-time hours, measured travel time or detour, and category-specific claims.
- Keeps every reported claim tied to a literal evidence excerpt.
- Refuses a candidate when a required claim is stale, missing, or weakly supported.
- Records live adapter responses so a run can be replayed offline.

## Install

Osusume requires Python 3.12 or newer, [`uv`](https://docs.astral.sh/uv/), and [`goplaces`](https://goplaces.sh/).

```sh
git clone https://github.com/mbelinky/osusume.git
cd osusume
uv sync
uv run osusume --help
```

A live run also needs:

- `GOOGLE_PLACES_API_KEY` for Google Places and Routes through `goplaces`.
- `EXA_API_KEY` for current web evidence.
- A local model command that accepts one JSON object on standard input and returns one JSON object on standard output.

## Try a search

```sh
uv run osusume find "a quiet ceramics studio with weekend hours" \
  --near "41.9,12.5" \
  --radius-km 4 \
  --when "2026-09-05T11:00:00+02:00" \
  --prefs "small workshop;not touristy" \
  --depth full \
  --json
```

Use `--depth quick` for a fast answer: it still reads the venue's own website and one linked menu page, but skips reviews, guides and press.

Two other scopes replace `--near`. A route search takes `--route FROM TO`, with `--max-detour-min` to cap the true detour. An anchored search takes a named place and a travel-time budget:

```sh
uv run osusume find "upscale or quirky cocktail bar" \
  --near-place "Anchor Bistro, Barcelona" \
  --max-min 8 \
  --mode walk \
  --when "2026-10-16T20:00:00+02:00" \
  --json
```

The anchor is resolved to a real listing and excluded from its own results. Each candidate is then gated on measured travel minutes from the anchor in the requested mode, not on straight-line distance, so a candidate fifteen minutes away cannot pass an eight-minute walk. `--mode` accepts `walk` (the default), `drive`, `bicycle`, or `transit`. A candidate whose travel time cannot be measured is reported unconfirmed rather than cleared.

## Evidence cards

Cards define what one category means in one country: search terms, languages, useful sources, required claims, and freshness windows.

```sh
uv run osusume card list
uv run osusume card show salumeria
```

The repository includes two reviewed examples for Italian salumerie and antique shops. Automatically drafted cards stay under `cards/drafts/` until you inspect and promote them.

```sh
uv run osusume card promote NAME
```

See [CARD_GUIDE.md](CARD_GUIDE.md) before adding a card.

## Model commands

The default model command is:

```text
codex exec --model MODEL --json -
```

Create `config/local.yaml` to use another locally authenticated command or different models:

```yaml
models:
  default_model: gpt-5-mini
  fallback_model: gpt-5-mini
  task_overrides: {}
  commands:
    default: [codex, exec, --model, "{model}", --json, -]
```

Each command receives `{"slot": "...", "payload": {...}}` and must return one JSON object. Tests replace all model and network calls with recorded fixtures.

## Replay and privacy

Every live run writes its request and raw adapter responses under `runs/`. Replay uses that directory without network access or credentials:

```sh
uv run osusume find --replay tests/fixtures/runs/e_f3 --json
```

The `runs/` directory is ignored by Git. It can still contain precise locations, searches, and source text, so treat it as private data.

## Development

```sh
uv run pytest -q
```

The offline suite covers stale evidence, detour math, venue hours, category rules, quote checks, source independence, refusal, run replay, and render-only output.

## License

Osusume is available under the [MIT License](LICENSE).
