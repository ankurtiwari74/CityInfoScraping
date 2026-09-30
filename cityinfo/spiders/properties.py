import base64
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import scrapy
from scrapy.exceptions import CloseSpider
from scrapy.spiders import Spider

from cityinfo.items import PropertyItem
from cityinfo.sessions import check_login

logger = logging.getLogger(__name__)

SRP_URL = "https://properties.cityinfoservices.com/properties-for-rent-in-bangalore/srp"
AJAX_URL = "https://properties.cityinfoservices.com/search/ajaxSearchResults/"
PAGE_SIZE = 10

# Site has two listing "shapes" sharing one SRP card layout, distinguished by
# detail URL suffix:
#  /pjd = "project" (a building, e.g. a coworking brand or tech park)
#  /prd = "property" (a single rentable unit/floor within a building)
# Recon: debug/detail_http_loggedout.html (pjd), debug/detail4_prd_loggedout.html (prd)


class PropertiesSpider(Spider):
    name = "properties"
    allowed_domains = ["properties.cityinfoservices.com"]

    def __init__(self, max_pages=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._max_pages_arg = max_pages
        self._login_verified = False
        # Detail URLs already scheduled, so repeated (cumulative) SRP
        # renders only yield genuinely new detail requests.
        self._seen_detail_urls = set()

    async def start(self):
        settings_max = self.settings.get("MAX_PAGES")
        self.max_pages = int(self._max_pages_arg) if self._max_pages_arg else settings_max
        yield scrapy.Request(
            SRP_URL,
            callback=self.parse_srp,
            meta={
                "zyte_api": {"httpResponseBody": True, "httpResponseHeaders": True},
                "page": 1,
            },
            errback=self.errback_log,
        )

    def errback_log(self, failure):
        self.crawler.stats.inc_value("requests_failed")
        logger.error("Request failed: %s", failure)

    # ------------------------------------------------------------------
    # Login verification
    # ------------------------------------------------------------------
    def _verify_login(self, response):
        if self._login_verified:
            return
        if not check_login(response):
            raise CloseSpider("login failed: logged-out marker still present on SRP page")
        self._login_verified = True
        logger.info("Login verified: logged-out marker absent on %s", response.url)

    # ------------------------------------------------------------------
    # SRP parsing.
    #
    # Pagination note: the site's own "Load More" JS refuses to run for a
    # logged-out user (redirects to signup), which looks like login-gated
    # pagination. But the AJAX endpoint it calls
    # (POST /search/ajaxSearchResults/) has no server-side login check: a
    # plain anonymous POST returns real results (verified against a
    # standalone script calling the Zyte API directly). Driving the same
    # POST through scrapy.FormRequest + scrapy-zyte-api's transparent-mode
    # automap reproducibly got HTTP 200 with an empty body instead (tried
    # disabling the Zyte session, disabling Scrapy's cookie jar, and
    # spoofing a browser User-Agent -- none of it changed the outcome);
    # clicking #loadMore via browserHtml actions was flakier still (worked
    # once, silently no-opped on repeated clicks in two other runs). What
    # actually works: send a fully-explicit Zyte API payload (below) that
    # bypasses automap's Request-to-params translation entirely, using the
    # exact body/headers verified against the standalone script.
    # ------------------------------------------------------------------
    def parse_srp(self, response):
        page = response.meta["page"]
        if page == 1:
            self._verify_login(response)

        cards = response.css("div.listing-block")
        new_count = 0

        for card in cards:
            parsed = parse_srp_card(card, response)
            if parsed is None:
                continue
            self.crawler.stats.inc_value("srp_cards_found")
            if parsed["detail_url"] in self._seen_detail_urls:
                continue
            self._seen_detail_urls.add(parsed["detail_url"])
            new_count += 1
            yield scrapy.Request(
                parsed["detail_url"],
                callback=self.parse_detail,
                meta={
                    "zyte_api": {"httpResponseBody": True, "httpResponseHeaders": True},
                    "srp_title": parsed["title"],
                    "srp_location": parsed["location"],
                    "srp_operator": parsed["operator"],
                    "srp_type": parsed["type"],
                    "srp_available_seats": parsed["available_seats"],
                    "srp_total_seats": parsed["total_seats"],
                    "source_page": page,
                },
                errback=self.errback_log,
            )

        logger.info("page %d -> %d links (%d new)", page, len(cards), new_count)
        self.crawler.stats.inc_value("pages_crawled")

        if new_count == 0:
            logger.info("Page %d yielded zero new links; stopping pagination.", page)
            return

        if self.max_pages and page >= self.max_pages:
            logger.info("Reached MAX_PAGES=%s; stopping pagination.", self.max_pages)
            return

        next_page = page + 1
        limit_from = len(self._seen_detail_urls)
        # The site computes its own internal page id as limitFrom/limit + 1
        # (see debug/search.search.js, loadMoreProperty()); sending a page
        # value inconsistent with limitFrom makes the endpoint return zero
        # results, so derive it rather than using our own page counter.
        ajax_page_id = limit_from // PAGE_SIZE + 1
        body = urlencode(build_ajax_pagination_params(ajax_page_id, limit_from))
        # Built as a fully-explicit Zyte API request (not scrapy.FormRequest
        # + transparent-mode automap) because automap reproducibly returned
        # HTTP 200 with an empty body for this endpoint, while this exact
        # payload -- verified byte-for-byte against a standalone script
        # hitting the Zyte API directly -- returns real results.
        yield scrapy.Request(
            AJAX_URL,
            method="POST",
            callback=self.parse_srp,
            meta={
                "zyte_api": {
                    "url": AJAX_URL,
                    "httpResponseBody": True,
                    "httpResponseHeaders": True,
                    "httpRequestMethod": "POST",
                    "httpRequestBody": base64.b64encode(body.encode()).decode(),
                    "customHttpRequestHeaders": [
                        {"name": "Content-Type", "value": "application/x-www-form-urlencoded"},
                        {"name": "X-Requested-With", "value": "XMLHttpRequest"},
                        {"name": "Referer", "value": SRP_URL},
                    ],
                },
                "page": next_page,
            },
            dont_filter=True,
            errback=self.errback_log,
        )

    # ------------------------------------------------------------------
    # Detail page parsing
    # ------------------------------------------------------------------
    def parse_detail(self, response):
        self._verify_login(response)

        canonical = response.css('link[rel="canonical"]::attr(href)').get()
        property_url = canonical or response.url

        title = clean(response.css("h1::text").get()) or response.meta.get("srp_title")

        address = None
        page_location = None
        operator = None
        for landmark in response.css("div.land-mark"):
            # On /pjd pages, operator and address/location live in separate
            # sibling div.land-mark elements. On /prd pages both live in the
            # SAME div (a <span>By X</span> next to an
            # <i class="locations">Location : Y</i>) -- joining all text in
            # the div indiscriminately (as an earlier version of this code
            # did) concatenates them into one garbage string. So: read the
            # i.locations child on its own for address/location, and read
            # only the div's own text plus any <span> text (explicitly
            # excluding i.locations) for the operator.
            loc_tag = landmark.css("i.locations")
            if loc_tag:
                loc_text = clean(" ".join(loc_tag.css("::text").getall()))
                if loc_text:
                    if re.match(r"(?i)^address\s*:", loc_text):
                        address = clean_label(loc_text, None, pattern=r"(?i)^address\s*:\s*")
                    elif re.match(r"(?i)^location\s*:", loc_text):
                        page_location = clean_label(loc_text, None, pattern=r"(?i)^location\s*:\s*")

            op_texts = landmark.xpath("./text() | ./span//text()").getall()
            op_text = clean(" ".join(op_texts))
            if op_text and re.match(r"(?i)^by\b", op_text):
                op = re.sub(r"(?i)^by\s*", "", op_text).strip()
                operator = op or None

        about_project = clean(
            " ".join(response.css(".cnt--abt .about-visible ::text, .cnt--abt .about-visible::text").getall())
        )

        amenities = [
            clean(a) for a in response.css(".amenities_toggleitem .option-name::text").getall()
        ]
        amenities = [a for a in amenities if a]

        floor_plan = [
            u for u in response.css("#floorImage img::attr(src)").getall() if u
        ]

        # Building-level Total Built-up Area / Area Available / Possession
        # Status live in a distinct block (div.prdtl_info_div) found only on
        # /pjd (project) pages -- /prd (single-unit) pages don't carry this
        # block at all, so prdtl_info stays empty there and the fields below
        # fall back to the nearest equivalent spec-list entries.
        prdtl_info = {}
        for row in response.css("div.prdtl_info_div"):
            label = clean(row.css("label::text").get())
            value = clean(" ".join(row.xpath("./text()").getall()))
            if label:
                prdtl_info[label.lower()] = value

        lat = response.css("#mapLatitude::attr(value)").get()
        lon = response.css("#mapLongitude::attr(value)").get()
        map_href = response.css('#LocationMap a[href*="maps"]::attr(href)').get()
        if lat and lon:
            location_map = f"https://www.google.com/maps?q={lat},{lon}"
        else:
            location_map = map_href

        # Generic label -> value spec dicts, merged from both known layouts.
        spec = {}
        for row in response.css(".spec-list"):
            label = clean(row.css(".spec-title::text").get())
            value = clean(row.css(".download_lnk::text").get())
            if label:
                spec[label.lower()] = value
        for row in response.css("ul.type-des > li"):
            texts = [clean(t) for t in row.css("::text").getall()]
            texts = [t for t in texts if t]
            if len(texts) >= 2:
                label, value = texts[0], " ".join(texts[1:])
                key = label.lower().rstrip(":").strip()
                if key == "type":
                    # The FEATURES spec-list block also has its own "Type"
                    # entry for coworking listings (e.g. "Co-working Space",
                    # a narrower sub-classification), but it's the type-des
                    # block at the top of the page (e.g. "Business Center &
                    # Co-working Space") that matches the SRP card's
                    # category and is the correct primary type -- so it
                    # must win over spec-list's value here, not just fill a
                    # gap.
                    spec[key] = value
                else:
                    spec.setdefault(key, value)

        available_seats = (
            spec.get("no. of seats offered")
            or spec.get("seats")
            or response.meta.get("srp_available_seats")
        )
        total_seats = spec.get("total billable seats") or response.meta.get("srp_total_seats")
        prop_type = spec.get("type") or response.meta.get("srp_type")

        total_built_up_area = prdtl_info.get("total built-up area")
        area_available = prdtl_info.get("area available")
        possession_status = (
            prdtl_info.get("possession status")
            or spec.get("timelines (possession by)")
            or spec.get("status")
        )
        no_of_floors = spec.get("no. of floors")
        per_floor_area = spec.get("per floor area (sq. ft.)") or spec.get("typical floor area (sq.ft)")
        year_of_completion = spec.get("year of completion")

        item = PropertyItem()
        item["title"] = title
        item["location"] = page_location or response.meta.get("srp_location")
        item["operator"] = operator or response.meta.get("srp_operator")
        item["type"] = prop_type
        item["available_seats"] = available_seats
        item["address"] = address
        item["total_seats"] = total_seats
        item["occupancy_certificate"] = spec.get("occupancy certificate")
        item["fire_noc"] = spec.get("fire noc")
        item["parking_facility"] = spec.get("parking facility")
        item["total_built_up_area"] = total_built_up_area
        item["area_available"] = area_available
        item["possession_status"] = possession_status
        item["no_of_floors"] = no_of_floors
        item["per_floor_area"] = per_floor_area
        item["year_of_completion"] = year_of_completion
        item["about_project"] = about_project
        item["floor_plan"] = floor_plan
        item["amenities"] = amenities
        item["location_map"] = location_map
        item["property_url"] = property_url
        item["scraped_at"] = datetime.now(timezone.utc).isoformat()
        item["source_page"] = response.meta.get("source_page")

        self.crawler.stats.inc_value("items_scraped")
        yield item

    def closed(self, reason):
        stats = self.crawler.stats.get_stats()
        logger.info("Spider closed (%s). Stats: %s", reason, json.dumps(stats, default=str))
        print_fill_rate_report(self.settings.get("OUTPUT_JSONL", "output/properties.jsonl"))


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def clean(value):
    if value is None:
        return None
    text = value.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def clean_label(value, prefix, pattern=None):
    text = clean(value)
    if text is None:
        return None
    if pattern:
        text = re.sub(pattern, "", text).strip()
    elif prefix and text.startswith(prefix):
        text = text[len(prefix):].strip()
    return text or None


def parse_srp_card(card, response):
    href = card.css("div.title-lst a::attr(href)").get()
    if not href:
        return None
    detail_url = response.urljoin(href)
    title = clean(card.css("div.title-lst a::text").get())

    loct_spans = card.css("span.loct")
    location = None
    operator = None
    if loct_spans:
        location = clean_label(loct_spans[0].css("::text").get(), "Location:")
    if len(loct_spans) > 1:
        operator = clean_label(loct_spans[1].css("::text").get(), "Operator:")
    else:
        plot_by = card.css("span.plot_By::text").get()
        if plot_by:
            op = re.sub(r"(?i)^by\s*", "", clean(plot_by) or "").strip()
            operator = op or None
    if operator:
        # Some project-type cards append " Project Type : X" onto the same
        # text node as the operator (e.g. industrial/warehouse listings);
        # that value is already captured separately via prj-specification.
        operator = re.sub(r"(?i)\s*Project Type\s*:.*$", "", operator).strip() or None

    prj_li = card.css("ul.prj-specification li::text").get()
    prop_type = None
    available_seats = None
    if prj_li:
        prop_type = clean_label(prj_li, "Type:")
        # .list-descript holds "Available Seats : N Seats" only for
        # coworking-style cards; for other property types (office/
        # industrial/warehouse) it holds an unrelated about-text snippet,
        # so only treat it as seats data when the label actually matches.
        list_descript = clean(card.css("div.list-descript::text").get())
        if list_descript and re.match(r"(?i)^available seats\s*:", list_descript):
            available_seats = clean_label(
                list_descript, None, pattern=r"(?i)^available seats\s*:\s*"
            )
    else:
        prop_lis = [clean(t) for t in card.css("ul.prop-specification li::text").getall()]
        prop_lis = [t for t in prop_lis if t]
        if prop_lis:
            prop_type = prop_lis[0]
        for extra in prop_lis[1:]:
            if re.search(r"(?i)seats?\s*:", extra):
                available_seats = re.sub(r"(?i)seats?\s*:\s*", "", extra).strip()

    # sqrft-rate is a seat range ("450 - 450 Seats") only for coworking
    # cards; for office/industrial/warehouse cards it's a built-up-area
    # range in Sq. Ft., which isn't part of our schema, so drop it.
    total_seats_raw = clean(card.css("span.sqrft-rate::text").get())
    total_seats = total_seats_raw if total_seats_raw and re.search(r"(?i)seat", total_seats_raw) else None

    return {
        "detail_url": detail_url,
        "title": title,
        "location": location,
        "operator": operator,
        "type": prop_type,
        "available_seats": available_seats,
        "total_seats": total_seats,
    }


def build_ajax_pagination_params(page, limit_from):
    return {
        "is_ajax": "1",
        "languageID": "",
        "languageKey": "",
        "searchCountryID": "",
        "searchCountryKey": "",
        "state": "",
        "city": "",
        "searchPropertyTypeKey": "",
        "searchPropertyAdded": "",
        "searchKeyword": "",
        "searchRegion": "",
        "searchPropertyPurpose": "Rent",
        "searchMinPrice": "",
        "searchMaxPrice": "",
        "searchPropertyTypeID": "",
        "searchAttributesStr": "",
        "searchType": "",
        "searchCityIDs": "",
        "searchAgencyIDs": "",
        "sortBy": "",
        "limitFrom": str(limit_from),
        "limitTo": str(PAGE_SIZE),
        "suggested": "0",
        "mainTab": "listTab",
        "searchLat": "",
        "searchLong": "",
        "searchRadius": "",
        "itemType": "Property",
        "searchUserTypeID": "",
        "url": SRP_URL,
        "Coordinates": "",
        "possessionStatus": "",
        "page": str(page),
    }


REPORT_FIELDS = [
    "title", "location", "operator", "type", "available_seats", "address",
    "total_seats", "occupancy_certificate", "fire_noc", "parking_facility",
    "total_built_up_area", "area_available", "possession_status",
    "no_of_floors", "per_floor_area", "year_of_completion",
    "about_project", "floor_plan", "amenities", "location_map", "property_url",
]


def print_fill_rate_report(jsonl_path):
    try:
        items = [json.loads(line) for line in open(jsonl_path, encoding="utf-8") if line.strip()]
    except FileNotFoundError:
        logger.warning("No output file at %s; skipping fill-rate report.", jsonl_path)
        return

    n = len(items)
    lines = [f"\n=== Fill-rate report ({jsonl_path}) ===", f"Total items: {n}"]
    if n:
        lines.append(f"{'field':24s} {'non-null':>10s} {'fill %':>8s}")
        for field in REPORT_FIELDS:
            if field in ("amenities", "floor_plan"):
                non_null = sum(1 for it in items if it.get(field))
            else:
                non_null = sum(1 for it in items if it.get(field) not in (None, ""))
            pct = 100.0 * non_null / n
            flag = "  <-- under 80%" if pct < 80 else ""
            lines.append(f"{field:24s} {non_null:10d} {pct:7.1f}%{flag}")
        urls = [it.get("property_url") for it in items]
        lines.append(f"\nDuplicate property_url count: {len(urls) - len(set(urls))}")
    report = "\n".join(lines)
    print(report)
    logger.info(report)
