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

## Restaurant guides (Spain, United Kingdom, France)

`cards/restaurant_es.yaml`, `cards/restaurant_gb.yaml` and `cards/restaurant_fr.yaml`
are the reviewed restaurant cards, chosen by the request country. Stage 2 uses
`registry/<cc>_restaurants.yaml` to qualify swept restaurants and inject missing
rated ones: Michelin stars in all three countries (Spain nationwide, Greater
London, Île-de-France only for France), Guía Repsol Soles in Spain, Guía
Macarfi for Barcelona and Madrid (rating 7 or more out of 10), Harden's Top 100
for the United Kingdom, Le Fooding's Paris selection, and The World's 50 Best
Restaurants (1 to 100) where an entry can be placed in one of these countries.
Gault & Millau is not seeded: its listing only renders through JavaScript. Each entry records the guide, level, official URL, locality,
province, verification date and, where the guide or a Places lookup gives it,
coordinates. Recommended Repsol restaurants and Michelin Bib Gourmand entries are
not included. See [coverage and discrepancies](registry/README.md).
`scripts/enrich_registry_locations.py --country ES --province Barcelona` adds
Places coordinates to rows that lack them so they can be scoped by distance.

The local registry needs no Exa key. Exa remains the fallback for a configured
source without local entries. Injected names resolve through Places, must match
the restaurant's identity, and pass the usual status, exclusions, distance, and
arrival-time gates. A guide award supports quality; it does not prove opening
hours, a tasting menu, availability, or any other requested attribute. Ephemeral
cards still have an empty guide lane.

Refresh with Python, `uv`, and `curl` installed:

```sh
uv run python scripts/refresh_guide_registry.py --country ES --dry-run
uv run python scripts/refresh_guide_registry.py --country ES
uv run python scripts/refresh_guide_registry.py --country GB
uv run python scripts/refresh_guide_registry.py --country FR
```

`--country` selects the guides (ES: Michelin, Repsol, 50 Best, Macarfi; GB:
Michelin, 50 Best, Harden's; FR: Michelin, 50 Best, Le Fooding) and the output
file. A rebuild keeps every earlier row's coordinates and Place ID by guide and URL. The refresh follows all Michelin star-filter pages
(the Île-de-France region listing for France) and the complete Repsol Soles
listing, and prints any 50 Best entry it cannot place in a country. Requests run serially, at least one second apart by default. Raw pages
and retrieval dates are cached locally under ignored `registry/raw/`; full HTML
is not committed because it can contain third-party script configuration.
The dry run caches pages and prints a unified diff without rewriting the seed.
`--reuse-cache` resumes an interrupted crawl; `--offline` rebuilds solely from
cached pages. Neither advances cached verification dates. Missing pages, count
mismatches, and inconsistent awards abort before replacing the registry.
The command also reports differences against the supplied Catalonia notes in
`registry/catalonia_notes.yaml`; those notes never become rating evidence.

## Configure

The defaults are in `config/default.yaml`. Local changes go to `config/local.yaml`:

```sh
uv run osusume config show
uv run osusume config set models.default_model '"gpt-5-mini"'
```

`models.default_model`, `models.fallback_model`, and `models.task_overrides` select models. `models.quick_task_overrides` applies on top of them for `--depth quick` runs only (for example a faster judge while someone is waiting in a chat; a full or deep-dive run keeps the stronger model). `models.commands` contains argument arrays. The default non-interactive shape is:

```sh
codex exec --model MODEL --json -
```

A lane-specific command can instead point at another locally authenticated CLI, for example `gemini --model MODEL --output-format json` or `claude --model MODEL --output-format json`. Each command receives one JSON object on standard input and must return one JSON object on standard output. Tests never invoke these commands.

`scripts/claude_lane.py` routes every slot through the Claude CLI. It prefers `claude-headless` (the account-slot wrapper) when it is on the PATH, because a bare `claude` reads its login from the macOS keychain, which a remote SSH session cannot open; `CLAUDE_LANE_BIN` overrides the choice. Set `models.photo_capable` to `false` when the configured lanes cannot read images: the engine then skips the photo fetches and the photo triage and read calls instead of paying for them and getting empty judgments back.

Ranking is review-count aware. Candidates sort by guide weight first, then by a Bayesian rating (`ranking.prior_rating` and `ranking.prior_weight`, defaults 4.2 and 50), so a 5.0 from twelve reviews no longer outranks a 4.8 from four thousand. A guide entry's weight is scaled by its level through `ranking.level_factors` (three stars count more than one). The sweep drops listings whose Places types share nothing with the card's `accept_types`, when the card declares them (`type_mismatch`: a caterer or a cooking school answering a restaurant sweep; Places gives every restaurant subtype the parent type `restaurant`, so `[restaurant]` is enough), dedupes by place before the gates, restricts text search to the request country, and keeps the best `retrieval.max_candidates` (20) after ranking rather than the first twenty in query order. `retrieval.max_registry_injections` (12) caps how many guide entries outside the sweep are resolved per run, best level first and nearest first. A run verifies the ranked candidates in batches until `top` of them survive the gates or `retrieval.refill_rounds` times `top` have been checked, so a closed or over-budget venue frees its slot for the next one. Cards are picked by request country (`restaurant_es.yaml` for Spain, `restaurant_gb.yaml` for the United Kingdom); a card marked `portable: true` answers in any country, which is how the Booking hotel card works outside Spain. The packet carries `swept` (candidates before the cut) and `rejected_counts` (rejections by reason) so a reader does not have to count rows.

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
