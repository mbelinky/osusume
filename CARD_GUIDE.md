# Category card guide

A category card teaches the common funnel which search words and claims belong to one category. It never changes the evidence rules.

## Template

```yaml
category: swimming_pool
country: IT
reviewed: false
auto_written: true
languages:
  en: [swimming pool, public pool]
  it: [piscina, piscina comunale]
places_types: [swimming_pool]
query_templates:
  - "{name} {city} orari sito ufficiale"
load_bearing_claims:
  - operational_status
  - hours_at_arrival
  - detour
  - product_inventory
freshness_overrides:
  product_inventory: 30
event_shaped: false
```

An automatic draft may contain only vocabulary, Places types, query templates, load-bearing claims, and tighter freshness limits. It must include `operational_status`, `hours_at_arrival`, and `detour`. It cannot declare `sources`.

## Write a reviewed card

1. Add English and local-language terms that a local person or business would use. Include common shop types, not adjectives such as “best.”
2. Choose Google Places types only as search hints. A type never proves inventory, layout, quality, status, or hours.
3. Write queries for the venue website, menus or catalogs, local-language coverage, and the attributes a caller is likely to ask about.
4. Add load-bearing claims that must be checked for this category. Keep all three core claims.
5. Add a source only after checking that it publishes rated or scored entries for this country and category. Roundups remain mentions. Give each source a ranking weight from 0 to 1.
6. Tighten freshness when the category changes quickly. A card cannot increase an engine limit.
7. Set `reviewed: true`, validate it with `osusume card show NAME`, and place it in `cards/`.

Source weights only order candidates. A weight cannot make a review, listicle, delivery listing, rating, or prior note prove a claim.

The food example in `cards/salumeria_it.yaml` checks products and counter service. The antiques example in `cards/antiques_it.yaml` checks inventory, layout, and guide quality. Both use Italian search vocabulary and stricter one-year limits for changeable claims.

Run `osusume card promote NAME` only after reviewing a draft. Promotion refuses to overwrite an existing reviewed card.


## Local restaurant registries

`cards/restaurant_es.yaml` is the Spanish restaurant example. Its English,
Spanish, and Catalan terms cover restaurants and tasting menus, with `restaurant`
and `fine_dining_restaurant` as Places search hints. It declares Michelin and
Guía Repsol at weight 1.0 and limits quality evidence to one year. The core claims
and quality are load-bearing; concrete menu and wine requirements come from the
parsed request.

A reviewed restaurant card can use `registry/<country>_restaurants.yaml` (lowercase
country code). The YAML envelope has `format_version: 1`, `country: ES`, and an
`entries` list. Each row requires `name`, `locality`, `province`, `guide`
(`michelin` or `repsol`), `level` (1, 2, or 3), `url`, and `verified_at` (ISO date).
Optional coordinates improve scope filtering; explicit aliases handle genuine
name variants. Only guide names declared in the card's country-specific `sources`
are used. A guide contributes its ranking weight once per venue, even if several
rows resolve to the same Places listing.

Refresh and review changes with:

```sh
uv run python scripts/refresh_guide_registry.py --country ES --dry-run
uv run python scripts/refresh_guide_registry.py --country ES
uv run osusume card show restaurant_es
uv run pytest
```

Review additions, removals, award changes, and reported discrepancies with the
supplied notes. Never turn a Recommended entry, roundup, or unverified note into
a star/Sol entry. Local entries retain their verification dates in the evidence
ledger; they cannot establish status, hours, or arrival feasibility. Draft cards
cannot declare sources and cannot use the seed registry.
