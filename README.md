# Osusume

Osusume finds real-world places and events, then refuses claims that do not meet its source and freshness rules. The Python code decides every gate. Model calls parse the ask, judge literal evidence excerpts, and phrase only frozen claim rows.

## Install

Requirements are Python 3.12 or newer, `uv`, and the installed `goplaces` CLI.

```sh
uv sync
uv run osusume --help
```

Live runs need `GOOGLE_PLACES_API_KEY` for `goplaces` and `EXA_API_KEY` for web retrieval. The model lanes use local vendor CLIs, not direct paid API calls. Web retrieval is capped per candidate by `retrieval.max_pages_per_candidate` (60 by default), so one candidate cannot use another candidate's allowance. A card can set `official_link_terms`; matching homepage links are followed one level deep, up to `retrieval.official_pages_per_venue` (4 by default).

Fetched official pages and named-room passages are cached in `index/rooms.sqlite` for 30 days by default. Room-level hotel claims use that cache first and only fall back to web mining when the official site yields no room passage.

## Configure

The defaults are in `config/default.yaml`. Local changes go to `config/local.yaml`:

```sh
uv run osusume config show
uv run osusume config set models.default_model '"gpt-5-mini"'
```

`models.default_model`, `models.fallback_model`, and `models.task_overrides` select models. `models.commands` contains argument arrays. The default non-interactive shape is:

```sh
codex exec --model MODEL --json -
```

A lane-specific command can instead point at another locally authenticated CLI, for example `gemini --model MODEL --output-format json` or `claude --model MODEL --output-format json`. Each command receives one JSON object on standard input and must return one JSON object on standard output. Tests never invoke these commands.

## Run

One route-scoped live run:

```sh
uv run osusume find "Roscioli-style deli with mortadella cut to order" \
  --route "Orvieto" "Rome" \
  --when "2026-09-02T13:00:00+02:00" \
  --max-detour-min 15 \
  --prefs "not touristy" \
  --exclude "Moretti" \
  --card salumeria \
  --depth full \
  --contact-drafts \
  --json
```

One anchor-scoped live run, bounded by walking time from a named place:

```sh
uv run osusume find "upscale or quirky cocktail bar" \
  --near-place "Anchor Bistro, Barcelona" \
  --max-min 8 \
  --mode walk \
  --when "2026-10-16T20:00:00+02:00" \
  --depth full \
  --json
```

One hotel run uses Booking.com for candidates and live rates, then verifies each hotel through the normal Places and evidence gates:

```sh
uv run osusume find "4 or 5 star pet-friendly hotel with breakfast and free cancellation" \
  --near-place "Plaça de Catalunya, Barcelona" \
  --check-in 2026-10-01 \
  --check-out 2026-10-03 \
  --adults 2 \
  --card hotel \
  --json
```

`--check-in` and `--check-out` are required for a Booking hotel sweep, and `--adults` defaults to 2. Use `--city NAME` to name the city for a city-wide `--near` search; it is optional with other scopes. The parse lane also reads the city and explicit star, guest-score, pets, breakfast, free-cancellation, and hot-tub filters from the ask. Osusume reads Booking's live filter sidebar once, matches its chips against required attributes, their synonyms, and the hotel card's aliases, then runs up to six chip searches before the broad search. Chip results come first, unique hotels are merged by Booking slug, and the merged total is capped at 120 by default. Because aliases and synonyms can describe the same hotels, `coverage.booking_total` is the largest matched chip count, not the sum. Chips select candidates but never prove a hotel or room claim; a private in-room hot tub still needs evidence from the hotel's own site or photos. When no chip matches, Osusume records an empty chip list and keeps the prior filtered-plus-broad search behavior. Osusume returns the Booking link and never books a room.

The anchor is resolved to a real listing, excluded from its own results, and
every candidate is gated on measured travel minutes in the requested mode.
`--mode drive` turns the same ask into "ten minutes by cab".

Each live run stores the input and every raw adapter response under `runs/`. Replay uses those files without network access or credentials:

```sh
uv run osusume find --replay tests/fixtures/runs/e_f3 --json
```

Use `--depth quick` for a fast answer: it still reads the venue's own website and the official pages selected by the card, or the first linked menu page when the card has no `official_link_terms`, but skips reviews, guides and press. Use `osusume card list`, `osusume card show NAME`, and `osusume card promote NAME` to inspect reviewed cards and promote an automatic draft after review.

## Test

```sh
uv run pytest -q
```

The test suite is offline. Its fixtures cover the six reported failures, stale evidence, detour math, venue contact upgrades, refusal, ledger freezing, quote checks, source independence, card limits, and render-only frozen claims.
