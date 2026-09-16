# Changelog

All notable changes to Osusume are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Osusume is in
super beta: minor versions can still change commands and output.

## [0.10.0] - 2026-09-16

### Added

- Michelin Bib Gourmand as its own guide, `michelin_bib`, read by the same
  crawler as the stars: a distinction table in `osusume/michelin_registry.py`
  binds each distinction to its listing filter, the guide its rows are stored
  under, and the two award readings that must agree. Every Bib row is level 1
  and the restaurant cards weight the guide 0.6, below Michelin stars and The
  World's 50 Best at 1.0 and Harden's at 0.8, so a Bib Gourmand never outranks
  a star. The United Kingdom gains 146 rows and Île-de-France 46, which takes
  London from 126 rated restaurants to 174 and Paris from 415 to 454 without
  altering a single existing row.
- Coordinates for rows their guides publish without them, through
  `scripts/enrich_registry_locations.py`: 243 more French rows and 41 more
  British ones, each kept only when the registry identity rules accept the
  Places match. Paris now carries coordinates on 421 of its 454 rows and London
  on 172 of 175, where Paris previously had 140, so travel-time gating now
  reaches most of both cities rather than a third of Paris.

## [0.9.0] - 2026-09-16

### Added

- Local guide registry for restaurants: `registry/es_restaurants.yaml` seeds
  every current Michelin-starred and Guía Repsol Soles restaurant in Spain,
  crawled from the official listings by `scripts/refresh_guide_registry.py`
  (polite, cached, offline and dry-run modes, atomic replacement after
  validation). The reviewed `cards/restaurant_es.yaml` (English, Spanish and
  Catalan vocabulary) uses it at stage 2 to weight swept restaurants and
  inject the rated ones the sweep missed, resolving identities through Places
  and applying each guide's weight once. A registry row backs every
  quality-type claim, so a "Michelin-starred or equivalent" ask can be
  supported in quick mode, where no web mining runs. Exa stays the fallback
  for a configured guide with no local entries; ephemeral cards keep an
  empty guide lane.
- Guide registries for the United Kingdom (`registry/gb_restaurants.yaml`,
  Michelin stars nationwide with Greater London complete) and France
  (`registry/fr_restaurants.yaml`, Michelin stars in Île-de-France), plus The
  World's 50 Best Restaurants for all three countries, with reviewed
  `cards/restaurant_gb.yaml` and `cards/restaurant_fr.yaml`.
- Registry identity matching folds accents, ignores generic words (restaurant,
  by, the locality) and hotel suffixes, and accepts token containment, so
  "Angle" matches "Angle Barcelona" and "Aleia" matches "Aleia Restaurant at
  Casa Fuster Hotel"; with coordinates on both sides the 300 m rule decides.
  A live run had rejected 27 of 77 injected entries on name alone.
- `scripts/enrich_registry_locations.py` adds Places coordinates to registry
  rows that have none.
- Three more guides: Guía Macarfi (Barcelona and Madrid, rating 7+ mapped to
  levels), Harden's Top 100 UK Restaurants, and Le Fooding's Paris selection.
  Registry injections are ordered by card weight, then level, then distance,
  so a Michelin star is never displaced by a Macarfi score.
- A run keeps verifying ranked candidates until `top` of them survive the
  gates or `retrieval.refill_rounds` times `top` have been checked, so a
  closed or over-budget venue no longer eats one of the five slots.
- Review-count-aware ranking (`ranking.prior_rating`, `ranking.prior_weight`)
  and level-scaled guide weights (`ranking.level_factors`).
- `models.photo_capable`: when false, no photo fetches or photo lanes run.
- `models.quick_task_overrides`: model overrides that apply to `--depth quick`
  only, so a chat-speed judge does not weaken full runs.
- Evidence the code produced itself (Places fields, computed routes, local
  registry rows, Booking rates and signals) is accepted literally; the judge
  model reads only page, review, and photo evidence and is not called when a
  candidate has none.
- Packet fields `swept` and `rejected_counts`.

### Changed

- The sweep dedupes by place before the gates (one rejection row per venue),
  rejects listings whose Places types share nothing with the card's
  `accept_types` when the card declares them (`type_mismatch`), restricts
  text search to the request country, and cuts to `retrieval.max_candidates`
  after ranking instead of taking the first twenty in query order. Cards are
  chosen by request country: a Spanish restaurant card no longer answers a
  London ask; a card marked `portable: true` (the Booking hotel card) still
  answers anywhere.
- Ranking now reads the Places CLI's `user_rating_count`; before, every live
  candidate had no review count and ranking fell back to raw rating.
- Snapshot format: runs recorded before 0.9.0 no longer replay or resume
  (the registry payload, judge payload, and judge call sequence changed).
- Judge calls run in parallel in every lane, Places details are fetched
  concurrently, and registry injections resolve concurrently (one call per
  distinct venue), recorded in candidate order so snapshots replay unchanged.
  `retrieval.max_registry_injections` (12) caps injections, best level first.
- The Claude lane prefers `claude-headless` so runs started over SSH no longer
  fail on the keychain, reports the failing binary and exit code, and sends each
  distinct page text once to the judge instead of once per claim.

### Fixed

- `--when` now sets both ends of the arrival window even when the parse lane
  proposed its own: a lane that widened a 20:30 dinner to 20:30-23:00 made
  every restaurant with a 21:30 last seating fail `hours_at_arrival`.

## [0.8.2] - 2026-09-11

### Added

- Judge calls run in parallel. In the room lane the per-venue judge model
  calls of a batch run concurrently (`retrieval.judge_workers`, default 4);
  responses are still recorded one by one in candidate order, so run
  snapshots replay unchanged. The warm Barcelona run was no faster than the
  cold one because these calls ran one after another.

### Fixed

- A sentence that continues the one before it ("Todas las habitaciones son
  amplias. Con bañera de hidromasaje ...") keeps the room tie of the previous
  sentence, so a hotel whose own page describes every room that way is
  code-accepted again; an independent hotel-level sentence after a rooms
  sentence still goes to the judge.

## [0.8.1] - 2026-09-10

### Fixed

- The cheap room proof accepted rooftop, spa and plain-bathtub passages as
  in-room hot tubs: a live Barcelona run cleared ten hotels and seven were
  wrong. Code now accepts a passage only when its room name names a room
  category (room, suite, penthouse, apartment, habitación, chambre, zimmer,
  camera and their plurals) or one sentence carries both a room word and
  the attribute;
  a passage or room name carrying shared-facility words (rooftop, azotea,
  solarium, pool, piscina, spa, gym, wellness, "en nuestra terraza",
  "top floor", "de la última planta", or opening hours) is never
  code-accepted unless the same sentence says private terrace, in-room,
  en la habitación or en la suite; it goes to the judge marked
  "shared-context words present". For hot-tub attributes a bare bañera,
  bathtub or baño no longer counts, only hidromasaje, jacuzzi, hot tub,
  whirlpool, spa bath or jetted tub. The judge instruction carries the same
  three rules.

## [0.8.0] - 2026-09-06

### Added

- Room-evidence index. Every venue's own pages (homepage plus followed room
  and suite links) are stored once with a content fingerprint, and room
  passages are extracted per named room; a venue younger than
  `retrieval.index_max_age_days` is served from the index with no fetch,
  failed fetches are remembered for `retrieval.index_failure_ttl_hours`.
- Cheap proof first. A required attribute is settled by code when one
  indexed passage ties it to a named room with no negation or exception,
  recorded as official exact-venue evidence with the verbatim quote; only
  ambiguous passages go to the judge, batched once per venue; venues with no
  mention stay unknown without a model call.
- For room-level hotel claims, web search and photo reading run only when
  the index holds nothing for a venue that has a website. Official pages
  are fetched concurrently across venues (`retrieval.fetch_workers`) with
  one request at a time per host. Coverage reports how many venues came
  from the index.

## [0.7.3] - 2026-09-06

### Added

- Chip-based hotel discovery. Before a hotel sweep the engine reads
  Booking's filter sidebar (every chip with its code and live count), matches
  the ask's attributes and synonyms to chips through the card's
  `chip_aliases`, runs one sweep per matched chip plus the broad sweep, and
  merges the rows. The packet's `coverage` now carries the matched chips and
  `booking_total`, so the answer can say how many hotels Booking lists for
  that feature and how many were checked. Chips choose candidates only;
  room-level claims are still proved from the hotel's own pages.

### Fixed

- Booking's hot-tub filter code is `hotelfacility=63` (Hot tub/Jacuzzi);
  `54` is the spa chip and is no longer used for hot-tub asks.

## [0.7.2] - 2026-09-06

### Fixed

- Booking's facility filters are treated as a lossy pre-filter, not a gate:
  when the ask carries one (hot tub, pets, breakfast, free cancellation) the
  sweep runs a second, unfiltered query and merges the rows, because
  Booking's filtered page can omit the very hotel named in the query. A
  hot-tub ask never rejects a row on Booking's flag; the claim is settled by
  evidence. `retrieval.booking_max_rows` now defaults to 50.

## [0.7.1] - 2026-09-06

### Fixed

- Four gaps found on a real case (a hotel whose own site states a terrace
  jacuzzi on its top suite and whirlpool baths in every room, yet the claim
  came back unknown):
  the venue-site fetch now follows room and suite links for hotels
  (card `official_link_terms`, up to `retrieval.official_pages_per_venue`
  pages) instead of only menu links; the venue's own pages now feed every
  claim rather than only product claims; the web-mining page budget is per
  candidate (`retrieval.max_pages_per_candidate`), so a deep dive no longer
  starves candidates after the third; and required attributes carry
  local-language synonyms (hot tub, jacuzzi, hidromasaje...) used in
  searches and shown to the judge.

## [0.7.0] - 2026-09-06

### Added

- Deep dive mode. `--top N` sets how many surviving candidates get the full
  verification (default 5, as before) and `--deep-dive` verifies every one
  of them in batches (`retrieval.deep_dive_batch`, default 5). When the
  model lane runs out of allowance mid-way the run renders what it has as
  `partial: true`, writes a checkpoint in the run directory, and
  `osusume find --resume <run-dir>` continues from there without repeating
  finished candidates. Every packet now carries a `coverage` block
  (candidates seen, verified, pending) and the human answer says
  "Checked N of M".

## [0.6.1] - 2026-09-05

### Added

- City-wide hotel searches: `--city "<name>"` names the Booking query for a
  point scope, and `hotel_filters.hot_tub` (from "hot tub", "jacuzzi",
  "whirlpool", "hidromasaje" in the ask) maps to Booking's verified hot-tub
  filter. Booking paging is capped by `retrieval.booking_max_rows` (default
  25: Booking serves the same first page for every offset today).

## [0.6.0] - 2026-09-04

### Added

- Hotel lane. A card can declare `sweep_source: booking`; candidates, live
  totals and hard filters then come from a locally authenticated Booking.com
  command-line tool instead of Google Places, and each candidate is resolved
  to its Places listing so status, location, travel time, website and photos
  work as before. New request fields `stay` (`--check-in`, `--check-out`,
  `--adults`) and `hotel_filters` (stars, guest score, pets, breakfast, free
  cancellation), which the parse step also reads from the ask. Booking's
  filter codes were verified live (stars, score, pets, `fc=2` free
  cancellation, `mealplan=1` breakfast).
- A `price` claim, required when a stay is given, supported by the Booking
  total as same-run dated evidence, plus Booking stars, score, cancellation
  and breakfast flags rendered as signals, and the property facilities as
  listing evidence for property-level claims. Room-level asks ("hot tub in
  the suite") still need the hotel's own website or photos.
- `cards/hotel_es.yaml` for hotels in Spain. Every cleared hotel carries its
  `booking_url`; the engine never books.

## [0.5.0] - 2026-09-04

### Added

- The fast pass (`--depth quick`) now reads the venue's own website and one
  linked menu page, at most two fetches per candidate, as official evidence.
  A quick answer can settle a drinks list or a product claim from the venue's
  own pages; it still skips reviews, guides and press. Full depth is
  unchanged.
- Identity binding for web evidence, ported from the trip planner's research
  module: a page supports a venue claim only when it proves it is that venue
  (Google place id, coordinates within 150 m, the full address, a matching
  phone number, or the venue's own domain). A page that only shares the name
  and neighbourhood is area-level and cannot support a claim; a page that
  contradicts the venue is dropped. The evidence clause shows the label.
- The venue's own Instagram or Facebook page counts as official
  (`official_social`), fresh for 30 days on hours and product claims, and
  only when it passes the identity check.
- Retrieval caps in `config/default.yaml` (`retrieval.max_queries_per_candidate`,
  `max_results_per_query`, `max_pages_per_run`); a run that hits a cap says
  so in the answer.

### Changed

- Evidence queries use the locality from the venue address when the scope
  has no city, and add the local-language name as an alias.

## [0.4.0] - 2026-09-04

### Added

- Cards can declare `source_domains`, the web domains a guide publishes on,
  and `contact_questions`, the venue question to draft per claim type and
  language. The salumeria card carries its counter question in Italian and
  English.
- A refusal now names the near misses: `widen_candidates` lists up to three
  places rejected only for being over the travel or detour budget, with
  their measured minutes, and the human answer adds "Just outside the
  budget: X (11 min walk)".

### Fixed

- Guide ratings count in live runs. A live search hit could never become a
  rated entry because the check required a structured field only test data
  carries, so the quality claim never cleared and guide weights never ordered
  candidates. A hit is now a rated entry when it sits on the guide's declared
  domain, names the venue in its title, is not a list page, and the guide
  weighs at least 0.5. Only the title is checked for list-page words, not the
  whole page.
- Venue contact drafts are written in the venue's language (Italian, Spanish,
  Catalan, English, French, Portuguese, German, English fallback) instead of
  always Italian, and the layout question comes from the card, so a bar is
  no longer asked about cured meats. A card without a question proposes no
  layout draft.

## [0.3.0] - 2026-09-03

### Added

- A reviewed category card for cocktail bars in Spain, with Spanish and
  Catalan search vocabulary and the guides that publish rated or awarded bar
  entries for that country.

### Fixed

- Subjective taste no longer becomes a hard requirement. A request like
  "upscale or quirky cocktail bar" was parsed into two required claims that no
  qualified evidence can settle, so every candidate refused however good it
  was. Character, mood, style and crowd words are now ranking signals, and an
  either/or taste phrase is one signal rather than two requirements. A
  concrete thing named inside a taste phrase, such as a craft cocktail menu,
  is still a required claim.

## [0.2.0] - 2026-09-03

### Added

- Anchor scope: `--near-place "<name or place id>"` searches around a named
  place and gates every candidate on measured travel time to it, with
  `--max-min` for the budget (default 10) and `--mode walk|drive|bicycle|transit`
  (default walk). "A cocktail bar within an eight minute walk of this
  restaurant" is now a first-class request.
- A `proximity` claim, computed from real directions rather than straight-line
  distance, recorded in the frozen claim ledger like the route detour claim.
  A candidate over budget is rejected as `travel_over_budget`, and a failed
  directions call leaves the claim unconfirmed instead of clearing it.
- The sweep radius for an anchor search is derived from the travel budget and
  mode, so a walking ask no longer searches a driving-sized area.
- An unresolvable anchor refuses the whole request (`anchor_unresolved`)
  instead of falling back to a wider search, and the resolved anchor is
  excluded from its own results.
- Offline test coverage for the anchor path: budget clearing and rejection,
  unknown travel time, anchor exclusion, unresolved anchors, derived radii,
  travel modes, and the new command-line flags.

### Changed

- The options offered with a refusal now match the search that was asked for:
  more minutes for an anchor search, more detour for a route search, more
  radius for a point search. Every refusal previously listed all of them.

## [0.1.0] - 2026-08-27

### Added

- First public super-beta release: the evidence-gated funnel over the
  `goplaces` command-line tool and Exa web retrieval, with point and route
  scopes, category cards, claim freezing, adversarial judging, refusal as a
  first-class answer, and offline run replay.
