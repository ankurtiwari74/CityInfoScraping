# Scrapy settings for the cityinfo project.
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BOT_NAME = "cityinfo"

SPIDER_MODULES = ["cityinfo.spiders"]
NEWSPIDER_MODULE = "cityinfo.spiders"

ADDONS = {
    "scrapy_zyte_api.Addon": 500,
}

ZYTE_API_KEY = os.environ["ZYTE_API_KEY"]
SITE_USER = os.environ["SITE_USER"]
SITE_PASS = os.environ["SITE_PASS"]

ZYTE_API_TRANSPARENT_MODE = True
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# scrapy-zyte-api session management: keeps the logged-in state pinned to a
# consistent Zyte session (IP/browser fingerprint) across many requests.
# See cityinfo/sessions.py for the login flow.
ZYTE_API_SESSION_ENABLED = True
ZYTE_API_SESSION_POOL_SIZE = 1
ZYTE_API_SESSION_MAX_ERRORS = 3

# NOTE: ROBOTSTXT_OBEY=True deadlocks when combined with
# ZYTE_API_SESSION_ENABLED=True: RobotsTxtMiddleware issues its own
# internal engine.download() call for robots.txt very early in spider
# startup, and that call never resolves once the session middleware also
# tries to attach/initialize a session for it (reproduced: zero downloader
# activity, spider hangs indefinitely until CLOSESPIDER_TIMEOUT).
# We instead checked debug/robots.txt manually: it only disallows
# */alert and */login/facebook, neither of which this spider touches.
ROBOTSTXT_OBEY = False

CONCURRENT_REQUESTS = 8
DOWNLOAD_DELAY = 0.5
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 20
AUTOTHROTTLE_TARGET_CONCURRENCY = 4.0

RETRY_TIMES = 3
HTTPERROR_ALLOW_ALL = False

ITEM_PIPELINES = {
    "cityinfo.pipelines.CleaningPipeline": 300,
    "cityinfo.pipelines.JsonlWriterPipeline": 800,
}

MAX_PAGES = int(os.environ.get("MAX_PAGES", "0")) or None  # None = unlimited

FEED_EXPORT_ENCODING = "utf-8"

LOG_LEVEL = "INFO"

SEEN_URLS_FILE = "output/.seen_urls.json"
OUTPUT_JSONL = "output/properties.jsonl"
