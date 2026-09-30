# CityInfoServices Rental Property Scraper

Scrapy + Zyte API spider that logs into
`properties.cityinfoservices.com` and scrapes commercial rental listings
for Bangalore into `output/properties.jsonl` / `output/properties.csv`.

## Setup

```bash
cd cityinfo_scraper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create `.env` (git-ignored) in this directory:

```
ZYTE_API_KEY=your_zyte_api_key
SITE_USER=your_site_email
SITE_PASS=your_site_password
```

## How the spider works

- **Login**: `cityinfo/sessions.py` defines a `scrapy-zyte-api`
  `SessionConfig` that logs in via Zyte `browserHtml` + actions (type
  email, type password, click submit) and pins that authenticated
  session for the rest of the crawl. `check_login()` looks for the
  absence of the logged-out header marker
  (`div.account a.login`) to confirm login succeeded, both once at
  startup (`CloseSpider` if it fails) and on every subsequent response
  (via `SessionConfig.check()`, which reinitializes the session if it
  ever drops).
- **SRP page 1** is a plain `httpResponseBody` GET (no browser
  rendering needed — recon confirmed all listing cards are in the raw
  HTML).
- **Pagination (page 2+)** calls the site's own AJAX endpoint
  (`POST /search/ajaxSearchResults/`) directly with `httpResponseBody`.
  This endpoint has no server-side login check (unlike the "Load More"
  button's client-side JS, which redirects anonymous users to signup),
  but it must be sent as a **fully-explicit Zyte API request** — routing
  the same POST through `scrapy.FormRequest` +
  `scrapy-zyte-api`'s transparent-mode automap reproducibly returned
  HTTP 200 with an empty body. See the long comment above `parse_srp` in
  `cityinfo/spiders/properties.py` for the full debugging trail.
- **Detail pages**: two listing "shapes" exist, distinguished by URL
  suffix — `/pjd` (a project/building, e.g. a coworking brand or tech
  park) and `/prd` (a single rentable unit/floor). Both are parsed with
  the same selectors where the markup overlaps, plus shape-specific
  fallbacks (see `parse_detail` and `parse_srp_card`).
- **Dedup / resumability**: `JsonlWriterPipeline` persists a seen-URL
  set to `output/.seen_urls.json` and appends to
  `output/properties.jsonl`, so re-running the spider resumes rather
  than restarting, and never writes a duplicate `property_url`.

## Running each phase

```bash
# Phase 2/3 — verify login, parse a single item
scrapy crawl properties -s CLOSESPIDER_ITEMCOUNT=1 -a max_pages=1

# Phase 4 — one SRP page only
scrapy crawl properties -a max_pages=1

# Phase 5 — full crawl (remove the cap; ~3100+ properties / ~300+ pages
# at time of writing — budget Zyte credits and runtime accordingly)
scrapy crawl properties

# Cap at N pages instead (recommended for a bounded run)
scrapy crawl properties -a max_pages=10
```

A fill-rate report prints automatically when the spider closes. To
re-run it against an existing output file (e.g. after resuming a
partial crawl) without re-crawling:

```bash
python report_fillrate.py output/properties.jsonl
```

### CSV export

```bash
python export_csv.py            # reads output/properties.jsonl, writes output/properties.csv
```

### Resuming

Re-running `scrapy crawl properties` picks up where it left off:
`output/.seen_urls.json` skips already-scraped properties, and
`output/properties.jsonl` is appended to, not overwritten. For
resumable *request scheduling* (so an interrupted crawl doesn't need to
re-walk pagination from page 1), also pass a `JOBDIR`:

```bash
scrapy crawl properties -s JOBDIR=crawls/properties
```

## Known limitations (see final report for details)

- `occupancy_certificate` and `fire_noc` were confirmed absent from
  every sampled listing across all encountered property types
  (coworking, tech park, industrial, warehouse, standalone building) —
  this site does not appear to expose these fields for rental listings,
  not a selector bug.
- `address` is only present on `/pjd`-type detail pages; `/prd`-type
  pages expose a `location` string instead, with no separate street
  address.
- `available_seats` / `total_seats` only apply to coworking-type
  listings; they are correctly `null` for plain office/industrial/
  warehouse space, which is priced/sized in Sq. Ft. instead.

## Debug artifacts

`debug/` contains raw HTML/JS captured during Phase 1 recon (SRP,
several detail-page variants, login page, the site's own JS bundles).
Kept for reference when re-verifying selectors against future markup
changes.
