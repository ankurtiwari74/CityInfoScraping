import json
import logging
import re
from pathlib import Path

from itemadapter import ItemAdapter
from scrapy.exceptions import DropItem

logger = logging.getLogger(__name__)

WHITESPACE_RE = re.compile(r"[ \t\xa0]+")
NEWLINES_RE = re.compile(r"\n{2,}")


def clean_text(value):
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").replace("​", "")
    text = WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = NEWLINES_RE.sub("\n\n", text)
    text = text.strip()
    return text or None


def to_int_or_raw(value):
    """Return an int if the value parses as one, else the cleaned raw string."""
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    match = re.search(r"[\d,]+", cleaned)
    if match:
        digits = match.group(0).replace(",", "")
        try:
            return int(digits)
        except ValueError:
            pass
    return cleaned


class CleaningPipeline:
    TEXT_FIELDS = [
        "title",
        "location",
        "operator",
        "type",
        "address",
        "occupancy_certificate",
        "fire_noc",
        "parking_facility",
        "total_built_up_area",
        "area_available",
        "possession_status",
        "no_of_floors",
        "per_floor_area",
        "year_of_completion",
        "about_project",
        "location_map",
        "property_url",
    ]

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)

        for field in self.TEXT_FIELDS:
            if field in adapter:
                adapter[field] = clean_text(adapter.get(field))

        for field in ("available_seats", "total_seats"):
            if field in adapter:
                adapter[field] = to_int_or_raw(adapter.get(field))

        for field in ("amenities", "floor_plan"):
            values = adapter.get(field)
            if values is None:
                adapter[field] = []
            else:
                cleaned = [clean_text(v) for v in values]
                adapter[field] = [v for v in cleaned if v]

        if not adapter.get("property_url"):
            spider.crawler.stats.inc_value("items_dropped/no_property_url")
            raise DropItem(f"Missing property_url: {item!r}")

        if not adapter.get("title"):
            spider.crawler.stats.inc_value("items_dropped/no_title")
            raise DropItem(f"Missing title: {item!r}")

        return item


class JsonlWriterPipeline:
    def __init__(self, jsonl_path, seen_path):
        self.jsonl_path = Path(jsonl_path)
        self.seen_path = Path(seen_path)
        self.seen_urls = set()
        self._fh = None

    @classmethod
    def from_crawler(cls, crawler):
        settings = crawler.settings
        return cls(
            jsonl_path=settings.get("OUTPUT_JSONL", "output/properties.jsonl"),
            seen_path=settings.get("SEEN_URLS_FILE", "output/.seen_urls.json"),
        )

    def open_spider(self, spider):
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        if self.seen_path.exists():
            try:
                self.seen_urls = set(json.loads(self.seen_path.read_text()))
            except (json.JSONDecodeError, OSError):
                self.seen_urls = set()
        logger.info("Resuming with %d previously-seen property URLs.", len(self.seen_urls))
        self._fh = self.jsonl_path.open("a", encoding="utf-8")

    def close_spider(self, spider):
        if self._fh:
            self._fh.close()
        self.seen_path.write_text(json.dumps(sorted(self.seen_urls)))

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        url = adapter.get("property_url")

        if url in self.seen_urls:
            spider.crawler.stats.inc_value("items_dropped/duplicate_url")
            raise DropItem(f"Duplicate property_url: {url}")

        self.seen_urls.add(url)
        self._fh.write(json.dumps(dict(adapter), ensure_ascii=False) + "\n")
        self._fh.flush()
        spider.crawler.stats.inc_value("items_written")
        return item
