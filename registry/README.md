# Restaurant guide registry

One file per country (`es_restaurants.yaml`, `gb_restaurants.yaml`,
`fr_restaurants.yaml`), verified from the official guides on 2026-09-16. A
restaurant rated by two guides has one entry per guide.

| File | Guide | 3 / 2 / 1 | Entries | Focus city | City 3 / 2 / 1 | City entries |
| --- | --- | --- | --- | --- | --- | --- |
| es | Michelin | 16 / 37 / 245 | 298 | Barcelona | 4 / 5 / 18 | 27 |
| es | Michelin | | | Madrid | 1 / 6 / 22 | 29 |
| es | Repsol | 46 / 173 / 589 | 808 | Barcelona | 5 / 15 / 35 | 55 |
| es | Repsol | | | Madrid | 6 / 25 / 58 | 89 |
| es | 50 Best | 2 / 2 / 3 | 7 | Barcelona | 0 / 1 / 1 | 2 |
| es | Macarfi | 26 / 103 / 988 | 1,117 | Barcelona | 14 / 52 / 382 | 448 |
| es | Macarfi | | | Madrid | 10 / 42 / 409 | 461 |
| gb | Michelin | 10 / 23 / 165 | 198 | London | 6 / 15 / 63 | 84 |
| gb | 50 Best | 0 / 2 / 2 | 4 | London | 0 / 2 / 2 | 4 |
| gb | Harden's | 20 / 30 / 50 | 100 | London | 6 / 14 / 18 | 38 |
| fr | Michelin | 9 / 21 / 108 | 138 | Paris | 9 / 20 / 94 | 123 |
| fr | 50 Best | 1 / 3 / 1 | 5 | Paris | 1 / 3 / 0 | 4 |
| fr | Le Fooding | 0 / 0 / 288 | 288 | Paris | 0 / 0 / 288 | 288 |

The files hold 2,230 entries for Spain, 302 for the United Kingdom and 431 for
France. Macarfi's city counts read the Barcelona (08001-08042) and Madrid
(28001-28055) postcodes in each row's address, because Macarfi's own
`locality` is a district such as `L'Eixample Esquerre`, not the city.

Level 3, 2 and 1 mean three, two and one Michelin stars; three, two and one
Repsol Soles; rank 1-10, 11-50 and 51-100 of The World's 50 Best Restaurants;
a Macarfi rating of 9 or better, 8 to 8.9 and 7 to 7.9 out of 10; and rank
1-20, 21-50 and 51-100 of Harden's Top 100 UK Restaurants. Le Fooding
publishes a selection rather than a rating, so every Le Fooding row is level 1.
These are current listing coverage, not a claim that every restaurant remains
operational.

Each row keeps its official name, locality, province, guide, award level, URL
and `verified_at`. Michelin rows also carry coordinates, street address and
postcode; 50 Best and Harden's rows carry the published `rank`; Macarfi rows
carry the published `rating`, address and coordinates; Le Fooding rows carry
the address, postcode and the guide's own editorial category. Where a Repsol or 50 Best
restaurant uniquely matches a Michelin name in the same town, its coordinates
are reused with `location_source_url`; other rows use locality for initial
filtering. Places identity and Stage 1/arrival gates still apply.

## How each guide was crawled

`scripts/refresh_guide_registry.py --country ES|GB|FR` runs that country's
guides and rewrites its file atomically. Raw pages and retrieval metadata are
cached in local, ignored `registry/raw/`; `--dry-run`, `--offline` and
`--reuse-cache` behave as before.

- **Michelin Spain** follows the [Spain starred listing](https://guide.michelin.com/en/es/restaurants/3-stars-michelin/2-stars-michelin/1-star-michelin)
  through all its pages, then verifies each restaurant's award and postcode on
  its detail page. Province comes from the Spanish postcode prefix. Empty global
  detail pages fall back to the guide's English UK/US editions, recorded as
  `detail_url`.
- **Michelin United Kingdom** follows the [United Kingdom starred listing](https://guide.michelin.com/en/gb/restaurants/3-stars-michelin/2-stars-michelin/1-star-michelin)
  (198 restaurants, the whole country). Locality comes from the listing's
  `data-dtm-city` and province from `data-dtm-region`, so London entries read
  `Greater London`. Each detail page must confirm the award and an address in
  `GBR`.
- **Michelin France** is restricted to the guide's own Île-de-France region
  listing, [/en/fr/ile-de-france/](https://guide.michelin.com/en/fr/ile-de-france/restaurants/3-stars-michelin/2-stars-michelin/1-star-michelin)
  (138 restaurants), because the whole-country listing holds 646. The listing
  reports the region as `Ile-de-France`, which is what `province` stores; the
  detail page must confirm the award and an address in `FRA`.
- **The World's 50 Best Restaurants** reads the current edition's official list
  page, [/list/1-50](https://www.theworlds50best.com/list/1-50), which serves
  both halves of the ranking in its HTML; the crawl refuses a page that does not
  rank 1 to 100. Rank 1-10 becomes level 3, 11-50 level 2 and 51-100 level 1.
  The list gives only rank, name and city, so the country comes from the site's
  own establishment directory in [sitemap.xml](https://www.theworlds50best.com/sitemap.xml)
  (`/discovery/Establishments/<country>/<city>/<name>.html`) when the directory
  city matches the list city, otherwise from the restaurant's own list profile
  page, whose address ends in the country. Entries of other countries are
  skipped.
- **Guía Macarfi** reads the two city listings Osusume seeds,
  [Barcelona](https://macarfi.com/es/bcn/restaurantes) and
  [Madrid](https://macarfi.com/es/mad/restaurantes), through the JSON their own
  page requests: the crawl sends the listing's `X-Requested-With:
  XMLHttpRequest` and `Accept: application/json` headers and walks `?page=N`
  (15 per page, 51 Barcelona pages and 53 Madrid pages). It refuses a run whose
  collected rows do not match the paginator's declared total. Ratings run 0 to
  10; 9 and above becomes level 3, 8 to 8.9 level 2, 7 to 7.9 level 1 and
  anything below 7 is not seeded. `locality` is the listing's own district or
  town and `province` its province. The listing publishes coordinates, so every
  Macarfi row can be scoped by distance.
- **Harden's** reads the single server-rendered
  [Top 100 UK Restaurants](https://www.hardens.com/top-100-uk-restaurants/)
  page and refuses anything that does not parse as a complete 1-100 ranking.
  Locality and province come from each entry's own link,
  `/az/restaurants/<town>/<postcode-prefix>/<slug>.htm`: a London postcode area
  (E, EC, N, NW, SE, SW, W, WC) reads `London` / `Greater London`, any other
  town is its own locality and province.
- **Le Fooding** walks its whole [restaurant guide](https://lefooding.com/restaurants)
  through `?page=N` (16 cards per page, 87 pages, 1,391 addresses) because the
  listing carries no city filter, then keeps the rows whose address postcode is
  a Paris one (75xxx). The crawl refuses a short page followed by a full one and
  a run that returns fewer than 500 addresses in total.

## Coverage limits and discrepancies

- **Gault & Millau France is not seeded.** Its listing,
  [fr.gaultmillau.com/fr/search/restaurant](https://fr.gaultmillau.com/fr/search/restaurant),
  server-renders only the first 20 cards as a pre-warm for a client-side search
  component, and no plain fetch pages past them: `?page=`, `?p=`, `?offset=`,
  `?start=` and `?pagination=` all return the same first 20, and the site has no
  numbered listing route. The only city route in its sitemap,
  `/fr/ile-de-france/paris/restaurants`, likewise stops at 20 cards, all in the
  1st arrondissement. Paging would need the page's JavaScript search client, so
  the guide was skipped rather than scraped from a mirror or executed.
- France is seeded for Île-de-France only. Starred restaurants in other French
  regions are not in the registry, so a request outside Paris falls back to the
  live lanes.
- Le Fooding also covers Belgium and the French regions; only its Paris rows are
  seeded. Of 1,391 addresses in the guide, 288 are in Paris.
- Harden's ranks the whole United Kingdom, so 62 of its 100 rows are outside
  London; they are kept, because the registry is per country.
- Macarfi's listing covers the Barcelona and Madrid provinces, not only the two
  cities: 613 Barcelona-province and 504 Madrid-province rows clear the rating
  floor, of which 448 and 461 carry a city postcode. Of the 1,545 restaurants
  the two listings publish, 428 are rated below 7 and are not seeded.
- Macarfi is a rating out of 10, not an award. Its level 3 sits beside three
  Michelin stars and three Repsol Soles when Stage 2 sorts injections by level,
  which is why the cards weight it 0.8, below the three award guides.
- Nine to ten 50 Best entries per run cannot be placed in a country from the
  official site: ranks 51-100 have no profile page of their own, and these names
  are also absent from the establishment directory. The refresh prints each one.
  Two of them would have been Spanish: **Aponiente** (rank 84, El Puerto de
  Santa María) and **Mugaritz** (rank 87, San Sebastián; the directory lists it
  under Errenteria, so the city does not confirm). They were not inserted.
- **Jan** (rank 50, Munich) shares its name with a French entry in the
  directory; its own profile page places it in Germany, so it is skipped rather
  than filed under France.
- 50 Best rows store the list city in both `locality` and `province`, because
  the list publishes no region. Michelin rows in the same file use the guide's
  province or region, so the two guides sort differently within a country file.
- Adding the 50 Best rows to Spain re-derived the whole file: 1,106 entries
  before, 1,113 after, with no row lost and only one further change, a
  `detail_url` for Solla whose global page returned empty during this crawl.
- Adding Macarfi, Harden's and Le Fooding re-derived all three files from the
  same listings: Spain 1,113 → 2,230, the United Kingdom 202 → 302 and France
  143 → 431, with no row lost and no existing row changed. The refresh carries a
  previous file's `latitude`, `longitude`, `place_id`, `location_source` and
  `location_source_url` onto the row with the same guide and URL, so the 102
  Places lookups `scripts/enrich_registry_locations.py` resolved for Barcelona
  and Madrid Repsol rows survive a rebuild.

## Differences from the supplied Catalonia notes

The notes list 62 Michelin restaurants; the current official listing contains
60 (5 three-star, 9 two-star, 46 one-star). Teatro kitchen & bar and Escape are
absent from the current starred listing and were not inserted as starred entries.
Absence here does not establish closure or explain an award change.

All 60 matched entries have the star level in the notes. The exact differences
below include name/spelling changes; `catalonia_notes.yaml` preserves the original
notes and explicit comparison correspondences. No note supplies rating evidence.

- Name differs: Hermanos Torres → Cocina Hermanos Torres (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/cocina-hermanos-torres).
- Name differs: Enoteca → Enoteca Paco Pérez (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/enoteca204150).
- Name differs: L'Aliança 1919 → L'Aliança d'Anglès (Anglès; https://guide.michelin.com/en/catalunya/angles/restaurant/l-alianca-1919-d-angles).
- Name differs: COME → COME by Paco Méndez (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/come-by-paco-mendez).
- Name differs: Slow&Low → Slow & Low (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/slow-low).
- Name differs: Àngel → Angle (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/angle).
- Name differs: MAE → MAE Barcelona (Barcelona; https://guide.michelin.com/en/catalunya/barcelona/restaurant/mae-barcelona).
- Missing from current starred listing: Teatro kitchen & bar (Barcelona), notes 1 star(s).
- Missing from current starred listing: Escape (Barcelona), notes 1 star(s).
- Name differs: Diego's Corner → Rincón de Diego (Cambrils; https://guide.michelin.com/en/catalunya/cambrils/restaurant/rincon-de-diego).
- Name differs: Emporium → Empòrium (Castelló d'Empúries; https://guide.michelin.com/en/catalunya/castello-d-empuries/restaurant/emporium).
- Name differs: Delirium → Deliranto (Salou; https://guide.michelin.com/en/catalunya/salou/restaurant/deliranto).
- Name differs: Castell Peralada → Castell Perelada (Peralada; https://guide.michelin.com/en/catalunya/peralada/restaurant/castell-peralada).

Repsol confirms Suto and Sato i Tanaka at one Sol. Sensato and Os-kuro appear
under category R, now labeled “Restaurante Guía Repsol” (the former Recommended
category), and are excluded from the Soles seed. Bib Gourmand and Recommended
restaurants are not represented as star/Sol entries.
