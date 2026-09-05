# Changelog

All notable changes to Osusume are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Osusume is in
super beta: minor versions can still change commands and output.

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
