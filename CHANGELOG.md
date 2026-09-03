# Changelog

All notable changes to Osusume are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Osusume is in
super beta: minor versions can still change commands and output.

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
