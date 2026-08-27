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
